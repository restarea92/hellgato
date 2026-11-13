use crate::http;
use serde_json::Value;
use std::{
    fs,
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    time::{Duration, Instant},
};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

pub struct CoreProcess {
    child: Child,
    _stdin: Option<ChildStdin>,
    pub endpoint: String,
    pub token: String,
    stopped: bool,
    #[cfg(windows)]
    _job: Job,
}

#[cfg(windows)]
struct Job(windows_sys::Win32::Foundation::HANDLE);
#[cfg(windows)]
impl Drop for Job {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0);
        }
    }
}
#[cfg(windows)]
fn assign_job(child: &Child) -> Result<Job, String> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    unsafe {
        let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if handle.is_null() {
            return Err("Cannot create runtime ownership job".into());
        }
        let job = Job(handle);
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            std::mem::size_of_val(&limits) as u32,
        ) == 0
            || AssignProcessToJobObject(handle, child.as_raw_handle()) == 0
        {
            return Err("Cannot assign runtime process ownership".into());
        }
        Ok(job)
    }
}

impl CoreProcess {
    pub fn start(bundle: &Path, state_dir: &Path, fake: bool) -> Result<Self, String> {
        let template: Value = serde_json::from_slice(
            &fs::read(bundle.join("runtime.json")).map_err(|error| error.to_string())?,
        )
        .map_err(|error| error.to_string())?;
        let node = bundle.join(
            template["node"]
                .as_str()
                .ok_or("Missing bundled Node runtime")?,
        );
        let entry = bundle.join(
            template["entry"]
                .as_str()
                .ok_or("Missing headless runtime entry")?,
        );
        let mut config = template["service"].clone();
        config["stateDir"] = serde_json::json!(state_dir);
        config["settingsPath"] = serde_json::json!(state_dir.join("settings.json"));
        config["legacyIdentityPath"] = serde_json::json!(default_state_dir(false)
            .parent()
            .unwrap()
            .join("settings.json"));
        config["fake"] = serde_json::json!(fake);
        config["ownedStdin"] = serde_json::json!(true);
        let adapters = template["adapters"]
            .as_array()
            .ok_or("Missing bundled adapters")?;
        config["builtInAdapters"] = serde_json::json!(adapters
            .iter()
            .filter_map(|path| path.as_str())
            .map(|path| bundle.join(path))
            .collect::<Vec<_>>());
        if !fake {
            config["hostProbe"] = serde_json::json!({"command":bundle.join(template["hostWorker"].as_str().ok_or("Missing host integration worker")?)});
            config["nativeUi"] = serde_json::json!({"dllPath":bundle.join(template["nativeUi"].as_str().ok_or("Missing native extension")?),"configPath":state_dir.join("native-ui.json")});
        } else {
            config["apiPort"] = serde_json::json!(0);
            config["previewPort"] = serde_json::json!(0);
            config
                .as_object_mut()
                .ok_or("Invalid service configuration")?
                .remove("nativeUi");
        }
        fs::create_dir_all(state_dir).map_err(|error| error.to_string())?;
        let config_path = state_dir.join("runtime-config.json");
        fs::write(
            &config_path,
            serde_json::to_vec_pretty(&config).map_err(|error| error.to_string())?,
        )
        .map_err(|error| error.to_string())?;
        let mut command = Command::new(node);
        command
            .arg(entry)
            .arg("--config")
            .arg(config_path)
            .current_dir(bundle)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        let mut child = command.spawn().map_err(|error| error.to_string())?;
        #[cfg(windows)]
        let job = match assign_job(&child) {
            Ok(job) => job,
            Err(error) => {
                let _ = child.kill();
                return Err(error);
            }
        };
        let stdin = child.stdin.take();
        let stdout = child.stdout.take().ok_or("Missing runtime output")?;
        let stderr = child.stderr.take().ok_or("Missing runtime diagnostics")?;
        let (ready_sender, ready_receiver) = std::sync::mpsc::sync_channel(1);
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if line.len() > 131072 {
                    break;
                }
                if let Ok(value) = serde_json::from_str::<Value>(&line) {
                    if value["event"] == "service-ready" || value["event"] == "service-failed" {
                        let _ = ready_sender.try_send(value);
                    }
                }
            }
        });
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                if line.len() > 131072 {
                    break;
                }
            }
        });
        let report = ready_receiver.recv_timeout(Duration::from_secs(15));
        let descriptor: Result<Value, String> = (|| {
            let report = report.map_err(|_| "Headless runtime did not become ready".to_string())?;
            if report["event"] != "service-ready" {
                return Err(report["message"]
                    .as_str()
                    .unwrap_or("Runtime startup failed")
                    .into());
            }
            let descriptor: Value = serde_json::from_slice(
                &fs::read(state_dir.join("control.json")).map_err(|error| error.to_string())?,
            )
            .map_err(|error| error.to_string())?;
            if descriptor["pid"].as_u64() != Some(child.id() as u64) {
                return Err("Runtime descriptor belongs to a different process".into());
            }
            http::address(
                descriptor["endpoint"]
                    .as_str()
                    .ok_or("Missing runtime endpoint")?,
            )?;
            Ok(descriptor)
        })();
        let descriptor = match descriptor {
            Ok(value) => value,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
        };
        Ok(Self {
            child,
            _stdin: stdin,
            stopped: false,
            endpoint: descriptor["endpoint"].as_str().unwrap().into(),
            token: descriptor["token"]
                .as_str()
                .ok_or("Missing runtime token")?
                .into(),
            #[cfg(windows)]
            _job: job,
        })
    }
    pub fn is_running(&mut self) -> bool {
        self.child.try_wait().ok().flatten().is_none()
    }
    pub fn stop(&mut self) {
        if self.stopped {
            return;
        }
        self.stopped = true;
        let _ = http::request(
            &self.endpoint,
            &self.token,
            Some(("shutdown", &serde_json::json!({}))),
        );
        self._stdin.take();
        let deadline = Instant::now() + Duration::from_secs(6);
        while self.child.try_wait().ok().flatten().is_none() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(25));
        }
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
        }
        let _ = self.child.wait();
    }
}
impl Drop for CoreProcess {
    fn drop(&mut self) {
        self.stop();
    }
}

pub fn default_state_dir(fake: bool) -> PathBuf {
    let home = std::env::var_os("USERPROFILE")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    home.join("AppData/Local/Hellgato").join(if fake {
        "stream-dog-rust-fake"
    } else {
        "stream-dog-rust"
    })
}

pub fn prepare_host(bundle: &Path) -> Result<String, String> {
    let config: Value = serde_json::from_slice(
        &fs::read(bundle.join("runtime.json")).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    let executable = PathBuf::from(
        config["service"]["host"]["executable"]
            .as_str()
            .ok_or("Missing host executable")?,
    );
    let digest = config["service"]["host"]["executableSha256"]
        .as_str()
        .ok_or("Missing host digest")?;
    if hellgato_windows_host::file_digest(&executable).map_err(|error|error.to_string())? != digest.to_ascii_lowercase() {
        return Ok("호환성을 확인하지 않은 Stream Deck 버전입니다. 연결은 허용되지만 화면 확장 등 일부 기능에 문제가 있을 수 있습니다.".into());
    }
    let proxy = bundle.join(
        config["startupProxy"]
            .as_str()
            .ok_or("Missing reviewed startup proxy")?,
    );
    let plan = bundle.join(
        config["startupPlan"]
            .as_str()
            .ok_or("Missing reviewed startup plan")?,
    );
    match hellgato_windows_host::prepare::prepare(&executable, digest, &proxy, &plan) {
        Ok(value) => Ok(if value["changed"] == true {
            "준비가 완료됐습니다. Stream Deck를 직접 열어주세요."
        } else {
            "Stream Deck가 이미 준비돼 있습니다."
        }
        .into()),
        Err(error) if error.code == "HOST_PERMISSIONS" => {
            #[cfg(windows)]
            {
                elevated_prepare(bundle, &config, &executable, digest, &proxy, &plan)?;
                Ok("준비가 완료됐습니다. Stream Deck를 직접 열어주세요.".into())
            }
            #[cfg(not(windows))]
            {
                Err(error.to_string())
            }
        }
        Err(error) => Err(error.to_string()),
    }
}

#[cfg(windows)]
fn elevated_prepare(
    bundle: &Path,
    config: &Value,
    executable: &Path,
    digest: &str,
    proxy: &Path,
    plan: &Path,
) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::{
        Foundation::{CloseHandle, GetLastError},
        System::Threading::{GetExitCodeProcess, WaitForSingleObject},
        UI::Shell::{ShellExecuteExW, SHELLEXECUTEINFOW},
    };
    let wide = |value: &std::ffi::OsStr| value.encode_wide().chain(Some(0)).collect::<Vec<u16>>();
    let helper = bundle.join(
        config["hostWorker"]
            .as_str()
            .ok_or("Missing host preparation helper")?,
    );
    let helper = wide(helper.as_os_str());
    let arguments = format!(
        "--prepare --exe \"{}\" --sha256 {} --proxy \"{}\" --plan \"{}\"",
        executable.display(),
        digest,
        proxy.display(),
        plan.display()
    );
    let arguments = wide(std::ffi::OsStr::new(&arguments));
    let verb = wide(std::ffi::OsStr::new("runas"));
    unsafe {
        let mut info: SHELLEXECUTEINFOW = std::mem::zeroed();
        info.cbSize = std::mem::size_of_val(&info) as u32;
        info.fMask = 0x40;
        info.lpVerb = verb.as_ptr();
        info.lpFile = helper.as_ptr();
        info.lpParameters = arguments.as_ptr();
        info.nShow = 0;
        if ShellExecuteExW(&mut info) == 0 {
            return Err(format!(
                "관리자 권한 준비가 취소됐거나 실패했습니다. ({})",
                GetLastError()
            ));
        }
        if info.hProcess.is_null() {
            return Err("준비 프로세스를 확인할 수 없습니다.".into());
        }
        let wait = WaitForSingleObject(info.hProcess, 15000);
        let mut result = 1;
        let read = GetExitCodeProcess(info.hProcess, &mut result);
        CloseHandle(info.hProcess);
        if wait != 0 {
            return Err("준비 작업이 아직 진행 중입니다. 완료 후 상태를 확인하세요.".into());
        }
        if read == 0 || result != 0 {
            return Err("준비에 실패했습니다. Stream Deck를 종료했는지 확인하세요.".into());
        }
    }
    hellgato_windows_host::prepare::validate_owned(
        executable.parent().ok_or("Missing host folder")?,
    )
    .map_err(|error| error.to_string())?;
    Ok(())
}
