#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use hellgato_stream_dog::{bundle, chrome, process, smoke, tray, ui, ui_qa};
use std::path::PathBuf;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let value = |flag: &str| {
        args.iter()
            .position(|arg| arg == flag)
            .and_then(|index| args.get(index + 1))
            .map(PathBuf::from)
    };
    let fake = args.iter().any(|arg| arg == "--fake");
    let smoke = args.iter().any(|arg| arg == "--smoke");
    let state = value("--state-dir").unwrap_or_else(|| process::default_state_dir(fake));
    if args.iter().any(|arg| arg == "--stop") {
        tray::signal(&state, tray::Event::Quit);
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while tray::running(&state) && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(25));
        }
        if tray::running(&state) {
            std::process::exit(1);
        }
        return;
    }
    if !smoke && !args.iter().any(|arg| arg == "--ui-qa") && tray::signal(&state, tray::Event::Show)
    {
        return;
    }
    if let Some(directory) = value("--ui-qa") {
        ui_qa::run(directory, &args).expect("native UI rendering QA");
        return;
    }
    let root = value("--bundle").unwrap_or_else(|| {
        std::env::current_exe()
            .unwrap()
            .parent()
            .unwrap()
            .to_path_buf()
    });
    if let Err(error) = bundle::verify(&root) {
        if smoke {
            println!("{}", serde_json::json!({"error":error}));
            std::process::exit(1);
        }
        rfd::MessageDialog::new()
            .set_title("Hellgato Stream Dog")
            .set_description(error)
            .set_level(rfd::MessageLevel::Error)
            .show();
        return;
    }
    if smoke {
        match smoke::run(&root, &state) {
            Ok(value) => println!("{value}"),
            Err(error) => {
                println!("{}", serde_json::json!({"error":error}));
                std::process::exit(1);
            }
        }
        return;
    }
    let options = eframe::NativeOptions {
        viewport: eframe::egui::ViewportBuilder::default()
            .with_inner_size([960.0, 660.0])
            .with_min_inner_size([720.0, 500.0])
            .with_decorations(false)
            .with_icon(
                eframe::icon_data::from_png_bytes(chrome::APP_ICON).expect("embedded app icon"),
            ),
        ..Default::default()
    };
    let _ = eframe::run_native(
        "Hellgato Stream Dog",
        options,
        Box::new(move |cc| {
            Ok(Box::new(ui::StreamDog::new(
                cc,
                root,
                state,
                fake,
                args.iter().any(|arg| arg == "--background"),
            )))
        }),
    );
}
