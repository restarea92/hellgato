use crate::{
    backend::{Backend, Command, Status},
    chrome::{self, *},
};
use eframe::egui::{self, pos2, vec2, Color32, Rect, RichText};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    path::PathBuf,
    time::{Duration, Instant},
};

#[derive(Clone, Debug, PartialEq)]
pub enum Action {
    Api(&'static str, Value),
    Register,
    PrepareHost,
    BackupElsewhere(Value),
    Close,
    Quit,
    DismissPackage,
    DismissError,
}
#[derive(Clone, Copy, PartialEq)]
enum Page {
    Device,
    Settings,
    Adapters,
    Backups,
}
pub struct View {
    selected: String,
    page: Page,
    max_fps: u32,
    initialized: bool,
    about: bool,
    app_icon: egui::TextureHandle,
    dog_icon: egui::TextureHandle,
    samples: BTreeMap<String, (Instant, u64, f64)>,
    error_seen: Option<(String, Instant)>,
    backup_serial: String,
    rename_backup: Option<(String, String)>,
    delete_backup: Option<(String, String)>,
    pub hitboxes: BTreeMap<String, Rect>,
    pub layout: Option<chrome::Layout>,
}
pub struct StreamDog {
    backend: Backend,
    view: View,
    closing: bool,
    tray: Option<crate::tray::Tray>,
}
pub fn string<'a>(value: &'a Value, key: &str) -> &'a str {
    value[key].as_str().unwrap_or("")
}
fn owner<'a>(value: &'a Value, device: &Value) -> Option<&'a Value> {
    std::iter::once(value)
        .chain(value["sessions"].as_array().into_iter().flatten())
        .find(|state| {
            string(&state["settings"], "selectedDeviceId") == string(device, "id")
                && string(&state["settings"], "selectedAdapterId") == string(device, "adapterId")
        })
}
fn status(value: &Value) -> (&'static str, Color32) {
    if value["deviceConnected"] == true && (value["hostConnected"] == true || value["fake"] == true)
    {
        ("연결됨", SUCCESS)
    } else if value["runtime"]["desired"] == true {
        ("연결 대기", Color32::from_rgb(0xdf, 0xc0, 0x8e))
    } else {
        ("연결 안 됨", MUTED)
    }
}
fn local_error(message: &str) -> &str {
    if message.contains("Disconnect before") {
        "이 기기는 이미 연결되어 있습니다. 연결을 유지한 상태로 목록을 표시합니다."
    } else {
        message
    }
}
fn compatibility_warning(code: &str) -> Option<&'static str> {
    match code {
        "HOST_BUILD_UNTESTED" => Some("호환성을 확인하지 않은 Stream Deck 버전입니다. 일부 기능에 문제가 있을 수 있습니다."),
        "HOST_LAYOUT_UNPREPARED" => Some("Stream Deck 화면 확장이 준비되지 않았습니다. 키 배치가 다르게 표시될 수 있습니다."),
        "HOST_NATIVE_UI_UNAVAILABLE" => Some("Stream Deck 추가 설정을 연결하지 못했습니다. 노브 밝기 등 일부 기능을 사용할 수 없습니다."),
        "DEVICE_FIRMWARE_UNTESTED" => Some("호환성을 확인하지 않은 기기 펌웨어입니다. 일부 기능에 문제가 있을 수 있습니다."),
        _ => None,
    }
}
impl StreamDog {
    pub fn new(
        cc: &eframe::CreationContext<'_>,
        bundle: PathBuf,
        state: PathBuf,
        fake: bool,
        background: bool,
    ) -> Self {
        chrome::configure(&cc.egui_ctx);
        let tray = crate::tray::Tray::new(&state, cc.egui_ctx.clone()).ok();
        if background && tray.is_some() {
            cc.egui_ctx
                .send_viewport_cmd(egui::ViewportCommand::Visible(false));
        }
        Self {
            backend: Backend::start(bundle, state, fake, cc.egui_ctx.clone()),
            view: View::new(&cc.egui_ctx),
            closing: false,
            tray,
        }
    }
    fn close(&mut self) {
        if !self.closing {
            self.closing = true;
            self.backend.send(Command::Shutdown);
        }
    }
    fn hide(&mut self, context: &egui::Context) {
        if self.tray.is_some() {
            context.send_viewport_cmd(egui::ViewportCommand::Visible(false));
        } else {
            self.close();
        }
    }
}
impl View {
    pub fn new(context: &egui::Context) -> Self {
        Self {
            selected: String::new(),
            page: Page::Device,
            max_fps: 60,
            initialized: false,
            about: false,
            app_icon: chrome::texture(context, "hellgato", APP_ICON),
            dog_icon: chrome::texture(context, "stream-dog", DOG_ICON),
            samples: BTreeMap::new(),
            error_seen: None,
            backup_serial: String::new(),
            rename_backup: None,
            delete_backup: None,
            hitboxes: BTreeMap::new(),
            layout: None,
        }
    }
    pub fn open_settings(&mut self) {
        self.page = Page::Settings;
    }
    pub fn open_adapters(&mut self) {
        self.page = Page::Adapters;
    }
    pub fn open_backups(&mut self) { self.page = Page::Backups; }
    fn button(&mut self, ui: &mut egui::Ui, id: &str, text: &str, enabled: bool) -> bool {
        let response = ui.add_enabled(enabled, egui::Button::new(text));
        self.hitboxes.insert(id.into(), response.rect);
        response.clicked()
    }
    fn select(&mut self, value: &Value, device: &Value) {
        self.rename_backup = None;
        self.delete_backup = None;
        self.selected = format!("{}:{}", string(device, "adapterId"), string(device, "id"));
        self.max_fps = owner(value, device)
            .and_then(|state| state["settings"]["maxFps"].as_u64())
            .unwrap_or(60) as u32;
        self.page = Page::Device;
    }
    pub fn show(&mut self, ui: &mut egui::Ui, state: &Status, closing: bool) -> Vec<Action> {
        let value = &state.value;
        let mut actions = Vec::new();
        self.hitboxes.clear();
        if !self.initialized && value["schemaVersion"] == 1 {
            if let Some(device) = value["devices"].as_array().and_then(|items| {
                items
                    .iter()
                    .find(|device| {
                        string(device, "id") == string(&value["settings"], "selectedDeviceId")
                    })
                    .or_else(|| items.first())
            }) {
                self.select(value, device);
            }
            if value["devices"]
                .as_array()
                .is_none_or(|items| items.is_empty())
            {
                if let Some(adapter) = value["adapters"].as_array().and_then(|items| items.first())
                {
                    actions.push(Action::Api("discover", json!({"adapterId":adapter["id"]})));
                }
            }
            self.initialized = true;
        }
        if self.selected.is_empty() {
            if let Some(device) = value["devices"].as_array().and_then(|items| items.first()) {
                self.select(value, device);
            }
        }
        let selected = value["devices"].as_array().and_then(|items| {
            items.iter().find(|device| {
                format!("{}:{}", string(device, "adapterId"), string(device, "id")) == self.selected
            })
        });
        let device = selected.cloned().unwrap_or(Value::Null);
        let session = owner(value, &device).unwrap_or(&Value::Null);
        let available = !closing && !state.starting && value["schemaVersion"] == 1;
        let key = self.selected.clone();
        let counter = session["framesSubmitted"].as_u64().unwrap_or(0);
        let sample = self
            .samples
            .entry(key)
            .or_insert((Instant::now(), counter, 0.0));
        let elapsed = sample.0.elapsed().as_secs_f64();
        if elapsed >= 1.0 {
            sample.2 = counter.saturating_sub(sample.1) as f64 / elapsed;
            sample.0 = Instant::now();
            sample.1 = counter;
        }
        let fps = sample.2;
        let maximized = ui.ctx().input(|input| {
            input.viewport().maximized.unwrap_or(false)
                || input.viewport().fullscreen.unwrap_or(false)
        });
        let layout = chrome::Layout::new(ui.max_rect(), maximized);
        self.layout = Some(layout);
        layout.paint(ui);
        let mut close = false;
        chrome::caption(ui, layout, maximized, &mut close);
        if close {
            actions.push(Action::Close);
        }
        chrome::region(
            ui,
            "menus",
            Rect::from_min_size(layout.title.min + vec2(7.0, 0.0), vec2(220.0, 34.0)),
            |ui| {
                ui.spacing_mut().item_spacing.x = 0.0;
                ui.spacing_mut().button_padding = vec2(7.0, 0.0);
                ui.style_mut()
                    .text_styles
                    .insert(egui::TextStyle::Button, egui::FontId::proportional(13.0));
                ui.spacing_mut().interact_size.y = 34.0;
                ui.visuals_mut().widgets.open.corner_radius = 0.into();
                ui.visuals_mut().widgets.hovered.corner_radius = 0.into();
                ui.visuals_mut().widgets.inactive.corner_radius = 0.into();
                ui.visuals_mut().widgets.inactive.weak_bg_fill = Color32::TRANSPARENT;
                ui.visuals_mut().widgets.inactive.bg_stroke = egui::Stroke::NONE;
                ui.horizontal_centered(|ui| {
                    ui.add(egui::Image::new((self.app_icon.id(), vec2(16.0, 16.0))));
                    ui.add_space(8.0);
                    let response = ui
                        .menu_button("파일", |ui| {
                            menu_style(ui);
                            if menu_item(ui, "어댑터 추가…", "", available) {
                                actions.push(Action::Register);
                                ui.close();
                            }
                            if menu_item(ui, "기기 백업…", "", available && !session.is_null()) {
                                self.page = Page::Backups;
                                ui.close();
                            }
                            ui.separator();
                            if menu_item(ui, "설정…", "", true) {
                                self.page = Page::Settings;
                                ui.close();
                            }
                            if menu_item(ui, "프로그램 종료", "", true) {
                                actions.push(Action::Quit);
                                ui.close();
                            }
                        })
                        .response;
                    self.hitboxes.insert("menu:file".into(), response.rect);
                    ui.menu_button("보기", |ui| {
                        menu_style(ui);
                        if menu_item(ui, "기기 관리", "", true) {
                            self.page = Page::Device;
                            ui.close();
                        }
                        if menu_item(ui, "어댑터 관리", "", true) {
                            self.page = Page::Adapters;
                            ui.close();
                        }
                    });
                    ui.menu_button("도움말", |ui| {
                        menu_style(ui);
                        if menu_item(ui, "Stream Dog 정보", "", true) {
                            self.about = true;
                            ui.close();
                        }
                    });
                });
            },
        );
        chrome::region(
            ui,
            "toolbar",
            layout.toolbar.shrink2(vec2(12.0, 4.0)),
            |ui| {
                ui.horizontal_centered(|ui| {
                    ui.add(egui::Image::new((self.dog_icon.id(), vec2(26.0, 26.0))));
                    ui.label(RichText::new("Stream Dog").strong().size(13.0));
                    separator(ui);
                    ui.add(
                        egui::Label::new(
                            RichText::new(format!(
                                "기기  ›  {}",
                                selected
                                    .map(|device| string(device, "displayName"))
                                    .unwrap_or("선택 없음")
                            ))
                            .size(11.0),
                        )
                        .truncate(),
                    );
                });
            },
        );
        chrome::region(
            ui,
            "sidebar",
            layout.sidebar.shrink2(vec2(12.0, 14.0)),
            |ui| {
                ui.label(RichText::new("기기").size(11.0).color(MUTED));
                ui.add_space(8.0);
                egui::ScrollArea::vertical()
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        for entry in value["devices"].as_array().into_iter().flatten() {
                            let id =
                                format!("{}:{}", string(entry, "adapterId"), string(entry, "id"));
                            let row = owner(value, entry).unwrap_or(&Value::Null);
                            let (label, color) = status(row);
                            let (rect, response) = ui.allocate_exact_size(
                                vec2(ui.available_width(), 48.0),
                                egui::Sense::click(),
                            );
                            self.hitboxes
                                .insert(format!("device:{}", string(entry, "id")), rect);
                            if self.selected == id || response.hovered() {
                                ui.painter().rect_filled(
                                    rect,
                                    2,
                                    if self.selected == id {
                                        TABS
                                    } else {
                                        Color32::from_gray(0x5e)
                                    },
                                );
                            }
                            ui.painter().circle_filled(
                                pos2(rect.left() + 10.0, rect.top() + 16.0),
                                3.5,
                                color,
                            );
                            let painter = ui.painter().with_clip_rect(rect.shrink2(vec2(8.0, 0.0)));
                            painter.text(
                                pos2(rect.left() + 23.0, rect.top() + 15.0),
                                egui::Align2::LEFT_CENTER,
                                string(entry, "displayName"),
                                egui::FontId::proportional(12.0),
                                TEXT,
                            );
                            painter.text(
                                pos2(rect.left() + 23.0, rect.top() + 34.0),
                                egui::Align2::LEFT_CENTER,
                                label,
                                egui::FontId::proportional(10.0),
                                color,
                            );
                            if response.clicked() {
                                self.select(value, entry);
                            }
                            ui.add_space(3.0);
                        }
                        if value["devices"]
                            .as_array()
                            .is_none_or(|items| items.is_empty())
                        {
                            ui.weak("발견된 기기 없음");
                        }
                    });
            },
        );
        chrome::region(ui, "tabs", layout.tabs, |ui| {
            ui.spacing_mut().item_spacing.x = 0.0;
            ui.horizontal_centered(|ui| {
                for (label, page, id) in [
                    ("기기 관리", Page::Device, "tab:device"),
                    ("설정", Page::Settings, "settings"),
                    ("어댑터 관리", Page::Adapters, "adapters"),
                    ("백업", Page::Backups, "backups"),
                ] {
                    let width = ui
                        .painter()
                        .layout_no_wrap(label.into(), egui::FontId::proportional(12.0), TEXT)
                        .size()
                        .x
                        + 24.0;
                    let (rect, response) =
                        ui.allocate_exact_size(vec2(width, 28.0), egui::Sense::click());
                    self.hitboxes.insert(id.into(), rect);
                    ui.painter().rect_filled(
                        rect,
                        0,
                        if self.page == page {
                            SHELL
                        } else if response.hovered() {
                            Color32::from_gray(0x4b)
                        } else {
                            TABS
                        },
                    );
                    ui.painter().vline(
                        rect.right() - 0.5,
                        rect.y_range(),
                        egui::Stroke::new(1.0, DIVIDER),
                    );
                    ui.painter().text(
                        rect.center(),
                        egui::Align2::CENTER_CENTER,
                        label,
                        egui::FontId::proportional(12.0),
                        if self.page == page { TEXT } else { MUTED },
                    );
                    if response.clicked() {
                        self.page = page;
                    }
                }
            });
        });
        chrome::region(
            ui,
            "content",
            layout.content.shrink2(vec2(28.0, 24.0)),
            |ui| {
                ui.spacing_mut().scroll = egui::style::ScrollStyle::solid();
                egui::ScrollArea::vertical().auto_shrink([false,false]).show(ui,|ui|{
                match self.page {
                    Page::Backups=>{
                        ui.heading("기기 백업");ui.add_space(8.0);
                        ui.label(selected.map(|device|string(device,"displayName")).unwrap_or("기기를 선택하세요."));
                        ui.weak("선택한 기기의 프로필과 공용 플러그인·아이콘·설정을 함께 백업합니다.");
                        ui.weak("정확한 백업을 위해 Stream Deck를 종료한 상태에서 진행하세요.");
                        let serial=string(&session["settings"]["identity"],"childSerial");
                        let params=json!({"adapterId":device["adapterId"],"deviceId":device["id"]});
                        let backups=&value["backups"];let busy=backups["busy"]==true;
                        let enabled=available && !session.is_null() && !busy;
                        if !serial.is_empty() && self.backup_serial!=serial && !busy {
                            self.backup_serial=serial.into();self.rename_backup=None;self.delete_backup=None;
                            actions.push(Action::Api("backup-list",params.clone()));
                        }
                        ui.add_space(12.0);ui.horizontal_wrapped(|ui|{
                            if self.button(ui,"backup-create","기본 위치에 백업",enabled){actions.push(Action::Api("backup-create",params.clone()));}
                            if self.button(ui,"backup-external","다른 위치에 백업…",enabled){actions.push(Action::BackupElsewhere(params.clone()));}
                            if self.button(ui,"backup-refresh","새로고침",enabled){actions.push(Action::Api("backup-list",params.clone()));}
                        });
                        if busy{ui.horizontal(|ui|{ui.spinner();ui.label("백업 처리 중…");});}
                        if let Some(error)=backups["error"].as_str(){ui.colored_label(ERROR,format!("백업 처리 실패: {error}"));}
                        if let Some(path)=backups["lastPath"].as_str(){ui.colored_label(SUCCESS,"백업을 저장했습니다.");ui.weak(path);}
                        if string(backups,"serial")==serial {
                            if let Some(path)=backups["directory"].as_str(){ui.add_space(8.0);ui.weak(format!("기본 위치  {path}"));}
                            for (index,entry) in backups["entries"].as_array().into_iter().flatten().enumerate(){ui.add_space(10.0);card(ui,string(entry,"name"),|ui|{
                                ui.weak(format!("{} · 프로필 {}개 · 파일 {}개 · {:.1} MB",string(entry,"createdLabel"),entry["profiles"].as_u64().unwrap_or(0),entry["fileCount"].as_u64().unwrap_or(0),entry["bytes"].as_f64().unwrap_or(0.0)/1048576.0));
                                let id=string(entry,"id");let mut command=params.clone();command["id"]=json!(id);
                                ui.horizontal_wrapped(|ui|{
                                    if self.button(ui,&format!("backup-up:{id}"),"↑",enabled && index>0){let mut p=command.clone();p["offset"]=json!(-1);actions.push(Action::Api("backup-move",p));}
                                    if self.button(ui,&format!("backup-down:{id}"),"↓",enabled && index+1<backups["entries"].as_array().map_or(0,Vec::len)){let mut p=command.clone();p["offset"]=json!(1);actions.push(Action::Api("backup-move",p));}
                                    if self.button(ui,&format!("backup-rename:{id}"),"이름 변경",enabled){self.rename_backup=Some((id.into(),string(entry,"name").into()));}
                                    if self.button(ui,&format!("backup-delete:{id}"),"삭제",enabled){self.delete_backup=Some((id.into(),string(entry,"name").into()));}
                                });
                            });}
                            if !busy && backups["entries"].as_array().is_some_and(Vec::is_empty){ui.add_space(16.0);ui.weak("이 기기에 저장된 백업이 없습니다.");}
                        }
                    }
                    Page::Adapters=>{
                        ui.heading("어댑터 관리");ui.add_space(16.0);ui.horizontal(|ui|{if self.button(ui,"check-adapters","설치 상태 검사",available){actions.push(Action::Api("check-adapters",json!({})));}
if self.button(ui,"register",".hga 추가…",available){actions.push(Action::Register);}});ui.add_space(12.0);
                        for adapter in value["adapters"].as_array().into_iter().flatten(){card(ui,string(adapter,"name"),|ui|{
                            ui.horizontal_wrapped(|ui|{ui.label(format!("v{}",string(adapter,"version")));ui.weak(if adapter["source"]=="official" {"공식 제공"}else{"사용자 추가 · 안전성 미확인"});ui.colored_label(if adapter["available"]==true {SUCCESS}else{ERROR},if adapter["available"]==true {"설치 정상"}else{"파일 확인 필요"});});
                            if let Some(error)=adapter["error"].as_str(){ui.colored_label(ERROR,error);}
                            if self.button(ui,&format!("discover:{}",string(adapter,"id")),"기기 검색",available && adapter["available"]==true){actions.push(Action::Api("discover",json!({"adapterId":adapter["id"]})));}
                        });ui.add_space(10.0);}
                    }
                    Page::Settings=>{
                        ui.heading("설정");ui.add_space(16.0);card(ui,"스트림",|ui|{
                            ui.label(selected.map(|device|string(device,"displayName")).unwrap_or("기기를 선택하세요."));ui.horizontal(|ui|{ui.label("최대 프레임 속도");let response=ui.add(egui::Slider::new(&mut self.max_fps,1..=60).suffix(" fps"));self.hitboxes.insert("fps".into(),response.rect);});
                            ui.weak("화면이 바뀔 때만 전송합니다.");if self.button(ui,"save","저장·적용",available && !session.is_null()){actions.push(Action::Api("device-settings",json!({"adapterId":device["adapterId"],"deviceId":device["id"],"maxFps":self.max_fps})));}
                        });ui.add_space(14.0);card(ui,"Stream Deck",|ui|{ui.label("밝기·버튼 동작은 Stream Deck에서 설정합니다.");if self.button(ui,"prepare","Stream Deck 준비",available && value["fake"]!=true){actions.push(Action::PrepareHost);}});
                    }
                    Page::Device=>{
                        if let Some(device)=selected {
                            ui.heading(string(device,"displayName"));ui.add_space(6.0);ui.label(RichText::new(format!("모델  {}",string(device,"modelId"))).size(11.0).color(MUTED));
                            let serial=string(&session["settings"]["identity"],"childSerial");ui.label(RichText::new(format!("가상 일련번호  {}",if serial.is_empty(){"연결 시 부여"}else{serial})).size(11.0).color(MUTED));ui.add_space(18.0);
                            card(ui,"연결",|ui|{let (label,color)=status(session);ui.colored_label(color,if closing {"연결 종료 중"}else{label});
                                if let Some(address)=session["networkAddress"].as_str(){ui.label(format!("네트워크 주소  {address}"));}
                                if session["runtime"]["desired"]==true {if self.button(ui,"disconnect","연결 해제",available){actions.push(Action::Api("disconnect-device",json!({"adapterId":device["adapterId"],"deviceId":device["id"]})));}}
                                else if self.button(ui,"connect","연결",available){actions.push(Action::Api("connect-device",json!({"adapterId":device["adapterId"],"deviceId":device["id"]})));}
                                if let Some(error)=session["runtime"]["error"].as_str(){ui.colored_label(ERROR,local_error(error));}
                                if let Some(firmware)=session["firmware"].as_str(){ui.weak(format!("펌웨어  {firmware}"));}
                                for warning in session["warnings"].as_array().into_iter().flatten(){if let Some(message)=compatibility_warning(string(warning,"code")){ui.colored_label(Color32::from_rgb(0xdf,0xc0,0x8e),message);}}
                            });ui.add_space(14.0);card(ui,"스트림",|ui|{
                                let resolution=match(session["display"]["width"].as_u64(),session["display"]["height"].as_u64()){(Some(w),Some(h))=>format!("{w} × {h}"),_=>"연결 대기".into()};
                                metric(ui,"화면",&resolution);metric(ui,"전송",&format!("{fps:.1} fps  /  최대 {} fps",session["settings"]["maxFps"].as_u64().unwrap_or(60)));
                                metric(ui,"처리 시간",&session["timings"]["imageUpdateToSubmitted"]["meanMs"].as_f64().map(|ms|format!("{ms:.1} ms")).unwrap_or_else(||"—".into()));ui.label(RichText::new("화면 수신부터 USB 제출 완료까지").size(10.0).color(MUTED));
                            });
                        }else{ui.heading("기기를 선택하세요");if self.button(ui,"open-adapters","어댑터 관리",true){self.page=Page::Adapters;}}
                    }
                }
                if !state.notice.is_empty(){ui.add_space(12.0);ui.label(&state.notice);}
                if state.error.is_empty(){self.error_seen=None;}else if self.error_seen.as_ref().is_none_or(|(message,_)|message!=&state.error){self.error_seen=Some((state.error.clone(),Instant::now()));}
                if self.error_seen.as_ref().is_some_and(|(_,at)|at.elapsed()<Duration::from_secs(8)){ui.add_space(12.0);ui.horizontal_wrapped(|ui|{ui.colored_label(ERROR,local_error(&state.error));if ui.small_button("닫기").clicked(){actions.push(Action::DismissError);}});}
            });
            },
        );
        chrome::region(ui, "status", layout.status.shrink2(vec2(10.0, 0.0)), |ui| {
            ui.horizontal_centered(|ui| {
                let (label, color) = status(session);
                ui.label(
                    RichText::new(if state.starting {
                        "코어 시작 중"
                    } else {
                        label
                    })
                    .size(10.0)
                    .color(color),
                );
                separator(ui);
                ui.label(
                    RichText::new(string(&session["settings"]["identity"], "childSerial"))
                        .size(10.0),
                );
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    ui.label(RichText::new("Stream Dog").size(10.0));
                    separator(ui);
                    ui.label(
                        RichText::new(if session["hostConnected"] == true {
                            "Stream Deck 연결됨"
                        } else {
                            "Stream Deck 대기"
                        })
                        .size(10.0),
                    );
                });
            });
        });
        if self.about {
            egui::Window::new("Stream Dog 정보")
                .open(&mut self.about)
                .collapsible(false)
                .resizable(false)
                .show(ui.ctx(), |ui| {
                    ui.add(egui::Image::new((self.dog_icon.id(), vec2(48.0, 48.0))));
                    ui.strong("Hellgato Stream Dog");
                    ui.weak(env!("CARGO_PKG_VERSION"));
                });
        }
        if let Some((id,mut name))=self.rename_backup.clone(){
            let mut done=false;
            egui::Modal::new(egui::Id::new("rename-backup")).show(ui.ctx(),|ui|{ui.heading("백업 이름 변경");ui.text_edit_singleline(&mut name);ui.horizontal(|ui|{
                if ui.button("취소").clicked(){done=true;}
                if ui.add_enabled(!name.trim().is_empty() && name.chars().count()<=100,egui::Button::new("저장")).clicked(){actions.push(Action::Api("backup-rename",json!({"adapterId":device["adapterId"],"deviceId":device["id"],"id":id,"name":name})));done=true;}
            });});
            self.rename_backup=if done{None}else{Some((id,name))};
        }
        if let Some((id,name))=self.delete_backup.clone(){
            egui::Modal::new(egui::Id::new("delete-backup")).show(ui.ctx(),|ui|{ui.heading("백업 삭제");ui.label(format!("‘{name}’ 백업을 삭제하시겠습니까?"));ui.weak("현재 프로필과 플러그인은 유지됩니다.");ui.horizontal(|ui|{
                if ui.button("취소").clicked(){self.delete_backup=None;}
                if ui.button("삭제").clicked(){actions.push(Action::Api("backup-delete",json!({"adapterId":device["adapterId"],"deviceId":device["id"],"id":id})));self.delete_backup=None;}
            });});
        }
        if state.review.is_object() {
            egui::Modal::new(egui::Id::new("external-adapter-review")).show(ui.ctx(),|ui|{ui.set_max_width(470.0);ui.heading("외부 어댑터 설치");ui.label(format!("{} · v{}",string(&state.review,"name"),string(&state.review,"version")));ui.add_space(10.0);ui.colored_label(ERROR,string(&state.review,"warning"));for dll in state.review["dlls"].as_array().into_iter().flatten(){ui.weak(dll.as_str().unwrap_or(""));}ui.add_space(10.0);ui.label("해시 검사는 무결성 확인용이며 악성코드 검사나 안전성 인증이 아닙니다.");ui.horizontal(|ui|{if self.button(ui,"cancel-package","취소",true){actions.push(Action::DismissPackage);}
if self.button(ui,"trust-package","출처를 신뢰하며 설치",state.review["supportsPlatform"]==true){actions.push(Action::Api("install-package",json!({"path":state.review["path"],"packageHash":state.review["packageHash"],"acknowledgeExternalCode":true})));}});});
        }
        chrome::resize_edges(ui, maximized);
        actions
    }
}
fn menu_style(ui: &mut egui::Ui) {
    ui.set_width(300.0);
    ui.spacing_mut().item_spacing = vec2(0.0, 0.0);
    ui.spacing_mut().button_padding = vec2(0.0, 0.0);
    ui.visuals_mut().widgets.hovered.weak_bg_fill = Color32::from_gray(0x37);
}
fn menu_item(ui: &mut egui::Ui, label: &str, shortcut: &str, enabled: bool) -> bool {
    let (rect, _) = ui.allocate_exact_size(vec2(300.0, 22.0), egui::Sense::hover());
    let response = ui.interact(
        rect,
        ui.id().with(label),
        if enabled {
            egui::Sense::click()
        } else {
            egui::Sense::hover()
        },
    );
    if enabled && response.hovered() {
        ui.painter().rect_filled(rect, 2, Color32::from_gray(0x37));
    }
    let color = if enabled {
        TEXT
    } else {
        Color32::from_gray(0x75)
    };
    ui.painter().text(
        rect.left_center() + vec2(28.0, 0.0),
        egui::Align2::LEFT_CENTER,
        label,
        egui::FontId::proportional(12.0),
        color,
    );
    ui.painter().text(
        rect.right_center() - vec2(14.0, 0.0),
        egui::Align2::RIGHT_CENTER,
        shortcut,
        egui::FontId::proportional(11.0),
        color,
    );
    response.clicked()
}
fn separator(ui: &mut egui::Ui) {
    let (rect, _) = ui.allocate_exact_size(vec2(2.0, 20.0), egui::Sense::hover());
    ui.painter().rect_filled(rect, 0, DIVIDER);
    ui.painter().vline(
        rect.right() - 0.5,
        rect.y_range(),
        egui::Stroke::new(1.0, Color32::from_gray(0x60)),
    );
}
fn metric(ui: &mut egui::Ui, label: &str, value: &str) {
    ui.horizontal(|ui| {
        ui.add_sized(
            [100.0, 20.0],
            egui::Label::new(RichText::new(label).color(MUTED)),
        );
        ui.label(value);
    });
}
fn card(ui: &mut egui::Ui, title: &str, contents: impl FnOnce(&mut egui::Ui)) {
    egui::Frame::new()
        .fill(FIELD)
        .stroke(egui::Stroke::new(1.0, Color32::from_gray(0x48)))
        .corner_radius(5)
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            let (header, _) =
                ui.allocate_exact_size(vec2(ui.available_width(), 36.0), egui::Sense::hover());
            ui.painter().rect_filled(
                header,
                egui::CornerRadius {
                    nw: 5,
                    ne: 5,
                    sw: 0,
                    se: 0,
                },
                Color32::from_gray(0x3b),
            );
            ui.painter().text(
                header.min + vec2(14.0, 18.0),
                egui::Align2::LEFT_CENTER,
                title,
                egui::FontId::proportional(12.0),
                TEXT,
            );
            ui.painter().hline(
                header.x_range(),
                header.bottom(),
                egui::Stroke::new(1.0, Color32::from_gray(0x48)),
            );
            egui::Frame::new().inner_margin(14).show(ui, |ui| {
                ui.set_width(ui.available_width());
                contents(ui);
            });
        });
}
impl eframe::App for StreamDog {
    fn ui(&mut self, ui: &mut egui::Ui, _: &mut eframe::Frame) {
        for event in self
            .tray
            .as_ref()
            .map(|tray| tray.events())
            .unwrap_or_default()
        {
            match event {
                crate::tray::Event::Show => {
                    ui.ctx()
                        .send_viewport_cmd(egui::ViewportCommand::Visible(true));
                    ui.ctx()
                        .send_viewport_cmd(egui::ViewportCommand::Minimized(false));
                    ui.ctx().send_viewport_cmd(egui::ViewportCommand::Focus);
                }
                crate::tray::Event::Quit => self.close(),
            }
        }
        let state = self
            .backend
            .state
            .lock()
            .map(|state| state.clone())
            .unwrap_or_default();
        if ui.ctx().input(|input| input.viewport().close_requested()) && !state.stopped {
            ui.ctx()
                .send_viewport_cmd(egui::ViewportCommand::CancelClose);
            if !self.closing {
                self.hide(ui.ctx());
            }
        }
        if state.stopped {
            ui.ctx().send_viewport_cmd(egui::ViewportCommand::Close);
        }
        let mut actions = Vec::new();
        egui::CentralPanel::default()
            .frame(egui::Frame::NONE)
            .show(ui, |ui| {
                actions = self.view.show(ui, &state, self.closing);
            });
        for action in actions {
            match action {
                Action::Api(name, params) => self.backend.send(Command::Api(name, params)),
                Action::PrepareHost => self.backend.send(Command::PrepareHost),
                Action::Register => {
                    if let Some(path) = rfd::FileDialog::new()
                        .add_filter("Hellgato Adapter", &["hga"])
                        .pick_file()
                    {
                        self.backend.send(Command::InspectPackage(path));
                    }
                }
                Action::BackupElsewhere(mut params) => {
                    if let Some(path)=rfd::FileDialog::new().set_title("백업을 저장할 폴더 선택").pick_folder(){
                        params["destination"]=json!(path);
                        self.backend.send(Command::Api("backup-create",params));
                    }
                }
                Action::Close => self.hide(ui.ctx()),
                Action::Quit => self.close(),
                Action::DismissPackage => self.backend.send(Command::DismissPackage),
                Action::DismissError => self.backend.send(Command::DismissError),
            }
        }
        ui.ctx().request_repaint_after(Duration::from_millis(250));
    }
}
