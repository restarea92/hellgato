use crate::{http, process::CoreProcess};
use serde_json::{json, Value};
use std::{
    path::Path,
    time::{Duration, Instant},
};

pub fn run(bundle: &Path, state: &Path) -> Result<Value, String> {
    let tray = crate::tray::Tray::new(state, eframe::egui::Context::default())?;
    if !crate::tray::signal(state, crate::tray::Event::Show)
        || !crate::tray::signal(state, crate::tray::Event::Quit)
    {
        return Err("Tray IPC unavailable".into());
    }
    let deadline = Instant::now() + Duration::from_secs(2);
    let mut events = Vec::new();
    while events.len() < 2 && Instant::now() < deadline {
        events.extend(tray.events());
        std::thread::sleep(Duration::from_millis(5));
    }
    if events != [crate::tray::Event::Show, crate::tray::Event::Quit] {
        return Err("Tray events were not delivered".into());
    }
    drop(tray);
    let mut core = CoreProcess::start(bundle, state, true)?;
    let command =
        |action, params: Value| http::request(&core.endpoint, &core.token, Some((action, &params)));
    let discovered = command("discover", json!({"adapterId":"hellgato-simulator"}))?;
    let id = discovered["result"]["devices"][0]["id"]
        .as_str()
        .ok_or("Simulator discovery failed")?;
    command(
        "select",
        json!({"adapterId":"hellgato-simulator","deviceId":id}),
    )?;
    command("connect", json!({}))?;
    let deadline = Instant::now() + Duration::from_secs(15);
    let status = loop {
        let value = http::request(&core.endpoint, &core.token, None)?;
        if value["runtime"]["state"] == "running"
            && value["screensaverRestores"].as_u64().unwrap_or(0) >= 1
        {
            break value;
        }
        if Instant::now() > deadline {
            return Err("Simulator did not submit a complete restored frame".into());
        }
        std::thread::sleep(Duration::from_millis(25));
    };
    if status["display"]["width"] != 600 || status["display"]["height"] != 360 {
        return Err("Independent adapter geometry did not reach the runtime".into());
    }
    command("disconnect", json!({}))?;
    core.stop();
    Ok(
        json!({"nativeRuntime":true,"trayEvents":true,"independentAdapter":true,"fullFrame":[600,360],"previewEnabled":false,
        "overlayRestored":status["screensaverRestores"],"cleanShutdown":true}),
    )
}
