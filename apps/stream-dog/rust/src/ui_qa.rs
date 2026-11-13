use crate::{
    backend::Status,
    chrome,
    ui::{Action, View},
};
use eframe::egui;
use serde_json::json;
use std::{
    path::PathBuf,
    time::{Duration, Instant},
};

pub fn fixture(connected: bool) -> Status {
    Status {
        value: json!({"schemaVersion":1,"fake":false,
        "settings":{"selectedAdapterId":"mirabox","selectedDeviceId":"n4-test","maxFps":60,"identity":{"childSerial":"HGMOCK000003"}},
        "adapters":[{"id":"mirabox","name":"Mirabox N4 Pro","version":"0.1.0","source":"official","available":true},{"id":"hellgato-simulator","name":"8-key simulator","version":"0.1.0","source":"official","available":true}],
        "devices":[{"id":"n4-test","adapterId":"mirabox","displayName":"N4 Pro · USB","modelId":"n4-pro"}],
        "runtime":{"desired":connected,"state":if connected { "running" } else { "idle" }},
        "deviceConnected":connected,"hostConnected":connected,"framesSubmitted":128,
        "display":{"width":800,"height":480},"timings":{"imageUpdateToSubmitted":{"meanMs":12.4}}}),
        ..Default::default()
    }
}

struct RenderQa {
    view: View,
    state: Status,
    directory: PathBuf,
    frames: usize,
    requested: bool,
    started: Instant,
    scenario: String,
}

pub fn run(directory: PathBuf, args: &[String]) -> eframe::Result {
    std::fs::create_dir_all(&directory).expect("QA directory");
    let option = |name: &str, fallback: &str| {
        args.iter()
            .position(|arg| arg == name)
            .and_then(|index| args.get(index + 1))
            .cloned()
            .unwrap_or_else(|| fallback.into())
    };
    let width = option("--qa-width", "960")
        .parse::<f32>()
        .expect("QA width");
    let height = option("--qa-height", "660")
        .parse::<f32>()
        .expect("QA height");
    let scale = option("--qa-scale", "1")
        .parse::<f32>()
        .expect("QA pixel scale");
    let scenario = option("--qa-case", "connected");
    assert!(
        (720.0..=1920.0).contains(&width)
            && (500.0..=1200.0).contains(&height)
            && (1.0..=2.0).contains(&scale)
    );
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([width * scale, height * scale])
            .with_decorations(false)
            .with_icon(eframe::icon_data::from_png_bytes(chrome::APP_ICON).expect("app icon")),
        ..Default::default()
    };
    eframe::run_native(
        "Stream Dog UI QA",
        options,
        Box::new(move |cc| {
            chrome::configure(&cc.egui_ctx);
            cc.egui_ctx.set_pixels_per_point(scale);
            let mut state = fixture(scenario != "disconnected");
            if scenario == "error" {
                state.error = "기기 연결이 끊겼습니다. USB 연결을 확인하세요.".into();
            }
            Ok(Box::new(RenderQa {
                view: View::new(&cc.egui_ctx),
                state,
                directory,
                frames: 0,
                requested: false,
                started: Instant::now(),
                scenario,
            }))
        }),
    )
}

impl eframe::App for RenderQa {
    fn raw_input_hook(&mut self, _: &egui::Context, input: &mut egui::RawInput) {
        if self.scenario == "menu" && (self.frames == 2 || self.frames == 3) {
            if let Some(rect) = self.view.hitboxes.get("menu:file") {
                let pos = rect.center();
                input.events.push(egui::Event::PointerMoved(pos));
                input.events.push(egui::Event::PointerButton {
                    pos,
                    button: egui::PointerButton::Primary,
                    pressed: self.frames == 2,
                    modifiers: egui::Modifiers::NONE,
                });
            }
        }
    }
    fn ui(&mut self, ui: &mut egui::Ui, _: &mut eframe::Frame) {
        self.frames += 1;
        egui::CentralPanel::default()
            .frame(egui::Frame::NONE)
            .show(ui, |ui| {
                let actions = self.view.show(ui, &self.state, false);
                assert!(!actions.contains(&Action::Close));
            });
        if self.frames == 2 && self.scenario == "settings" {
            self.view.open_settings();
        }
        if self.frames == 2 && self.scenario == "adapters" {
            self.view.open_adapters();
        }
        if self.frames == 2 && self.scenario == "backups" { self.view.open_backups(); }
        let screenshot = ui.ctx().input(|input| {
            input.events.iter().find_map(|event| {
                if let egui::Event::Screenshot { image, .. } = event {
                    Some(image.clone())
                } else {
                    None
                }
            })
        });
        if let Some(image) = screenshot {
            let bytes: Vec<u8> = image
                .pixels
                .iter()
                .flat_map(|pixel| pixel.to_srgba_unmultiplied())
                .collect();
            image::save_buffer(
                self.directory.join("window.png"),
                &bytes,
                image.size[0] as u32,
                image.size[1] as u32,
                image::ColorType::Rgba8,
            )
            .expect("rendered image");
            let layout = self.view.layout.expect("shell layout");
            let rectangle =
                |rect: egui::Rect| json!([rect.min.x, rect.min.y, rect.max.x, rect.max.y]);
            let value = json!({"scenario":self.scenario,"pixels":image.size,"pixelsPerPoint":ui.ctx().pixels_per_point(),
                "title":rectangle(layout.title),"toolbar":rectangle(layout.toolbar),"sidebar":rectangle(layout.sidebar),
                "boundary":rectangle(layout.boundary),"tabs":rectangle(layout.tabs),"content":rectangle(layout.content),"status":rectangle(layout.status),
                "hitboxes":self.view.hitboxes.iter().map(|(id,rect)| (id.clone(),rectangle(*rect))).collect::<serde_json::Map<_,_>>()});
            std::fs::write(
                self.directory.join("layout.json"),
                serde_json::to_vec_pretty(&value).unwrap(),
            )
            .expect("rendered layout");
            ui.ctx().send_viewport_cmd(egui::ViewportCommand::Close);
        } else if self.frames >= 20
            && self.started.elapsed() >= Duration::from_millis(500)
            && !self.requested
        {
            self.requested = true;
            ui.ctx()
                .send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::default()));
        }
        if self.started.elapsed() > Duration::from_secs(15) {
            panic!("Native renderer did not return its frame");
        }
        ui.ctx().request_repaint_after(Duration::from_millis(20));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use eframe::egui::vec2;
    fn draw(
        context: &egui::Context,
        view: &mut View,
        state: &Status,
        events: Vec<egui::Event>,
    ) -> Vec<Action> {
        let mut actions = Vec::new();
        let input = egui::RawInput {
            screen_rect: Some(egui::Rect::from_min_size(
                egui::Pos2::ZERO,
                vec2(960.0, 660.0),
            )),
            events,
            ..Default::default()
        };
        let mut output = context.run_ui(input, |ui| {
            egui::CentralPanel::default()
                .frame(egui::Frame::NONE)
                .show(ui, |ui| {
                    actions = view.show(ui, state, false);
                });
        });
        output.textures_delta.clear();
        actions
    }
    fn click(context: &egui::Context, view: &mut View, state: &Status, id: &str) -> Vec<Action> {
        let position = view.hitboxes[id].center();
        let event = |pressed| egui::Event::PointerButton {
            pos: position,
            button: egui::PointerButton::Primary,
            pressed,
            modifiers: egui::Modifiers::NONE,
        };
        draw(
            context,
            view,
            state,
            vec![egui::Event::PointerMoved(position), event(true)],
        );
        draw(context, view, state, vec![event(false)])
    }
    #[test]
    fn backups_use_the_selected_device_and_keep_export_explicit() {
        let context=egui::Context::default();chrome::configure(&context);
        let mut view=View::new(&context);let mut state=fixture(true);
        for _ in 0..3 { draw(&context,&mut view,&state,vec![]); }
        view.open_backups();
        let actions=draw(&context,&mut view,&state,vec![]);
        assert!(actions.iter().any(|action| matches!(action,Action::Api("backup-list",_))));
        state.value["backups"]=json!({"serial":"HGMOCK000003","entries":[],"busy":false});
        draw(&context,&mut view,&state,vec![]);
        assert_eq!(click(&context,&mut view,&state,"backup-create"),vec![Action::Api("backup-create",json!({"adapterId":"mirabox","deviceId":"n4-test"}))]);
        assert_eq!(click(&context,&mut view,&state,"backup-external"),vec![Action::BackupElsewhere(json!({"adapterId":"mirabox","deviceId":"n4-test"}))]);
    }
    #[test]
    fn connect_disconnect_and_settings_use_the_rendered_controls() {
        let context = egui::Context::default();
        chrome::configure(&context);
        let mut view = View::new(&context);
        let mut state = fixture(false);
        for _ in 0..3 {
            draw(&context, &mut view, &state, vec![]);
        }
        let actions = click(&context, &mut view, &state, "connect");
        assert_eq!(
            actions,
            vec![Action::Api(
                "connect-device",
                json!({"adapterId":"mirabox","deviceId":"n4-test"})
            )]
        );
        state = fixture(true);
        draw(&context, &mut view, &state, vec![]);
        assert_eq!(
            click(&context, &mut view, &state, "disconnect"),
            vec![Action::Api(
                "disconnect-device",
                json!({"adapterId":"mirabox","deviceId":"n4-test"})
            )]
        );
        let tabs_before = [
            view.hitboxes["tab:device"],
            view.hitboxes["settings"],
            view.hitboxes["adapters"],
        ];
        let pointer = view.hitboxes["adapters"].center();
        draw(
            &context,
            &mut view,
            &state,
            vec![egui::Event::PointerMoved(pointer)],
        );
        assert_eq!(
            tabs_before,
            [
                view.hitboxes["tab:device"],
                view.hitboxes["settings"],
                view.hitboxes["adapters"]
            ]
        );
        click(&context, &mut view, &state, "adapters");
        draw(&context, &mut view, &state, vec![]);
        assert!(view.hitboxes.contains_key("check-adapters"));
        assert!(!view.hitboxes.contains_key("disconnect"));
        click(&context, &mut view, &state, "settings");
        draw(&context, &mut view, &state, vec![]);
        assert_eq!(
            click(&context, &mut view, &state, "save"),
            vec![Action::Api(
                "device-settings",
                json!({"adapterId":"mirabox","deviceId":"n4-test","maxFps":60})
            )]
        );
    }
    #[test]
    fn minimum_size_and_maximized_layout_keep_regions_separate() {
        for size in [vec2(720.0, 500.0), vec2(960.0, 660.0), vec2(1256.0, 718.0)] {
            for maximized in [false, true] {
                let rect = egui::Rect::from_min_size(egui::Pos2::ZERO, size);
                let layout = chrome::Layout::new(rect, maximized);
                assert_eq!(layout.boundary.width(), 3.0);
                assert_eq!(layout.sidebar.width(), 244.0);
                assert_eq!(layout.title.height(), 34.0);
                assert_eq!(layout.status.height(), 28.0);
                assert!(layout.content.width() >= 460.0 && layout.content.height() >= 360.0);
                assert!(layout.content.bottom() < layout.status.top());
                assert_eq!(layout.frame.left(), if maximized { 0.0 } else { 3.0 });
            }
        }
    }
    #[test]
    fn an_external_dll_package_requires_the_explicit_confirmation_control() {
        let context = egui::Context::default();
        chrome::configure(&context);
        let mut view = View::new(&context);
        let mut state = fixture(false);
        state.review = json!({"id":"external","name":"External","version":"1.0.0","path":"C:/example.hga","packageHash":"reviewed-hash","supportsPlatform":true,"warning":"外部 DLL · 안전성 미확인","dlls":["vendor/transport.dll"]});
        for _ in 0..3 {
            draw(&context, &mut view, &state, vec![]);
        }
        let actions = click(&context, &mut view, &state, "trust-package");
        assert_eq!(
            actions,
            vec![Action::Api(
                "install-package",
                json!({"path":"C:/example.hga","packageHash":"reviewed-hash","acknowledgeExternalCode":true})
            )]
        );
        state.review["supportsPlatform"] = json!(false);
        draw(&context, &mut view, &state, vec![]);
        assert!(click(&context, &mut view, &state, "trust-package").is_empty());
    }
}
