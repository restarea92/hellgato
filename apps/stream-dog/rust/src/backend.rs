use crate::{http, process::CoreProcess};
use eframe::egui;
use serde_json::Value;
use std::{
    path::PathBuf,
    sync::{
        mpsc::{self, Receiver, SyncSender},
        Arc, Mutex,
    },
    time::Duration,
};

pub enum Command {
    Api(&'static str, Value),
    Shutdown,
    PrepareHost,
    InspectPackage(PathBuf),
    DismissPackage,
    DismissError,
}
#[derive(Clone, Default)]
pub struct Status {
    pub value: Value,
    pub error: String,
    pub starting: bool,
    pub stopped: bool,
    pub notice: String,
    pub review: Value,
}
pub struct Backend {
    pub commands: SyncSender<Command>,
    pub state: Arc<Mutex<Status>>,
}

impl Backend {
    pub fn start(bundle: PathBuf, state_dir: PathBuf, fake: bool, context: egui::Context) -> Self {
        let (sender, receiver) = mpsc::sync_channel(16);
        let state = Arc::new(Mutex::new(Status {
            starting: true,
            ..Default::default()
        }));
        let worker_state = state.clone();
        std::thread::spawn(move || run(bundle, state_dir, fake, receiver, worker_state, context));
        Self {
            commands: sender,
            state,
        }
    }
    pub fn send(&self, command: Command) {
        if self.commands.try_send(command).is_err() {
            if let Ok(mut state) = self.state.lock() {
                state.error = "명령 처리 중입니다. 잠시 후 다시 시도하세요.".into();
            }
        }
    }
}

fn run(
    bundle: PathBuf,
    state_dir: PathBuf,
    fake: bool,
    commands: Receiver<Command>,
    state: Arc<Mutex<Status>>,
    context: egui::Context,
) {
    loop {
        let mut core = match CoreProcess::start(&bundle, &state_dir, fake) {
            Ok(core) => core,
            Err(error) => {
                if let Ok(mut value) = state.lock() {
                    value.starting = false;
                    value.error = error;
                }
                context.request_repaint();
                match commands.recv_timeout(Duration::from_secs(2)) {
                    Ok(Command::Shutdown) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
                    _ => continue,
                }
            }
        };
        if let Ok(mut value) = state.lock() {
            value.starting = false;
            value.error.clear();
        }
        let mut shutdown = false;
        loop {
            match commands.recv_timeout(Duration::from_millis(250)) {
                Ok(Command::Shutdown) | Err(mpsc::RecvTimeoutError::Disconnected) => {
                    shutdown = true;
                    break;
                }
                Ok(Command::Api(action, params)) => {
                    let result =
                        http::request(&core.endpoint, &core.token, Some((action, &params)));
                    if let Ok(mut value) = state.lock() {
                        if action == "install-package" && result.is_ok() {
                            value.review = Value::Null;
                        }
                        value.error = result.err().unwrap_or_default();
                    }
                }
                Ok(Command::InspectPackage(path)) => {
                    let result = http::request(
                        &core.endpoint,
                        &core.token,
                        Some(("inspect-package", &serde_json::json!({"path":path}))),
                    );
                    if let Ok(mut value) = state.lock() {
                        match result {
                            Ok(mut result) => {
                                result["result"]["path"] = serde_json::json!(path);
                                value.review = result["result"].clone();
                                value.error.clear();
                            }
                            Err(error) => value.error = error,
                        }
                    }
                }
                Ok(Command::DismissPackage) => {
                    if let Ok(mut value) = state.lock() {
                        value.review = Value::Null;
                    }
                }
                Ok(Command::DismissError) => {
                    if let Ok(mut value) = state.lock() {
                        value.error.clear();
                    }
                }
                Ok(Command::PrepareHost) => {
                    let result = crate::process::prepare_host(&bundle);
                    if let Ok(mut value) = state.lock() {
                        match result {
                            Ok(notice) => {
                                value.notice = notice;
                                value.error.clear();
                            }
                            Err(error) => value.error = error,
                        }
                    }
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
            }
            match http::request(&core.endpoint, &core.token, None) {
                Ok(value) => {
                    if let Ok(mut status) = state.lock() {
                        status.value = value;
                    }
                }
                Err(error) => {
                    if let Ok(mut value) = state.lock() {
                        value.error = error;
                    }
                }
            }
            context.request_repaint();
            if !core.is_running() {
                break;
            }
        }
        core.stop();
        if shutdown {
            break;
        }
        if let Ok(mut value) = state.lock() {
            value.starting = true;
            value.error = "코어 연결을 복구하고 있습니다.".into();
        }
        context.request_repaint();
    }
    if let Ok(mut value) = state.lock() {
        value.stopped = true;
    }
    context.request_repaint();
}
