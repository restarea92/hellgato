use hellgato_mirabox_rust::{
    descriptor,
    native::{Backend, NativeBackend},
    protocol::{emit, read_request, reserve_output, Controller, FakeBackend},
    AdapterError,
};
use serde_json::json;
use std::{
    path::PathBuf,
    sync::mpsc::{self, RecvTimeoutError},
    time::Duration,
};

fn run<B: Backend>(backend: B, fake: bool) -> Result<(), Box<dyn std::error::Error>> {
    reserve_output()?;
    let (sender, receiver) = mpsc::sync_channel(4);
    std::thread::spawn(move || {
        let stdin = std::io::stdin();
        let mut input = stdin.lock();
        loop {
            match read_request(&mut input) {
                Ok(Some(request)) => {
                    if sender.send(Ok(request)).is_err() {
                        break;
                    }
                }
                Ok(None) => break,
                Err(error) => {
                    let _ = sender.send(Err(error));
                    break;
                }
            }
        }
    });
    let mut ready = descriptor();
    ready["event"] = json!("ready");
    ready["simulation"] = json!(fake);
    emit(&ready)?;
    let mut controller = Controller::new(backend);
    let execution = (|| -> Result<(), Box<dyn std::error::Error>> {
        loop {
            match receiver.recv_timeout(Duration::from_millis(5)) {
                Ok(Ok(request)) => emit(&controller.execute(&request))?,
                Ok(Err(error)) => {
                    emit(&json!({"id":null,"error":error}))?;
                    return Err(Box::new(error));
                }
                Err(RecvTimeoutError::Disconnected) => break,
                Err(RecvTimeoutError::Timeout) => {}
            }
            for event in controller.poll()? {
                emit(&event)?;
            }
        }
        Ok(())
    })();
    let closed = controller.close();
    if let Err(error) = &closed {
        let _ = emit(&json!({"event":"state","state":"failed","error":error}));
    }
    execution?;
    closed?;
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let mut dll = None;
    let mut fake = false;
    let mut describe = false;
    while let Some(argument) = args.next() {
        match argument.as_str() {
            "--describe" => {
                describe = true;
            }
            "--compat-model" => {
                hellgato_mirabox_rust::model::select(&args.next().ok_or("Missing model")?)?
            }
            "--dll" => dll = Some(PathBuf::from(args.next().ok_or("Missing DLL path")?)),
            "--fake" => fake = true,
            _ => {
                return Err(Box::new(AdapterError::new(
                    "ERR_ARGUMENT",
                    "Use --describe, --fake, or --dll ABSOLUTE_PATH",
                )))
            }
        }
    }
    if describe {
        emit(&descriptor())?;
        return Ok(());
    }
    if fake {
        run(FakeBackend::default(), true)
    } else {
        run(
            NativeBackend::new(dll.ok_or("An explicit --dll path is required")?),
            false,
        )
    }
}
