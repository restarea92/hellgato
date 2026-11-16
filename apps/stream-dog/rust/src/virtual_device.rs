use crate::{
    backend::{Command, Status},
    http, virtual_device_window as window,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use eframe::egui::{self, pos2, vec2, Color32, Pos2, Rect, Stroke, Vec2};
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    sync::{
        mpsc::{SyncSender, TrySendError},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};

#[derive(Default)]
struct LatestFrame {
    pixels: Option<egui::ColorImage>,
    layout: Value,
    model: String,
    error: String,
}
enum Press {
    Move(window::Drag),
    Arrow {
        index: usize,
        side: usize,
    },
    Key(usize),
    Dial {
        index: usize,
        previous: Option<f32>,
        angle: f32,
        moved: bool,
    },
    Touch {
        start: Pos2,
        at: Instant,
    },
}

pub struct VirtualDevice {
    pub visible: bool,
    adapter: String,
    device: String,
    scale: f32,
    preferred_scale: f32,
    latest: Arc<Mutex<LatestFrame>>,
    texture: Option<egui::TextureHandle>,
    layout: Value,
    model: String,
    pressed: Option<Press>,
    hovered_dial: Option<usize>,
    pending: VecDeque<Value>,
    commands: SyncSender<Command>,
    interactive: bool,
    native_configured: bool,
    native_alpha: u8,
    native_extent: [u32; 2],
}

impl VirtualDevice {
    pub fn start(
        adapter: String,
        device: String,
        commands: SyncSender<Command>,
        status: Arc<Mutex<Status>>,
        context: egui::Context,
    ) -> Arc<Mutex<Self>> {
        let latest = Arc::new(Mutex::new(LatestFrame::default()));
        let state = Arc::new(Mutex::new(Self {
            visible: true,
            adapter: adapter.clone(),
            device: device.clone(),
            scale: 0.5,
            preferred_scale: 0.5,
            latest: latest.clone(),
            texture: None,
            layout: Value::Null,
            model: String::new(),
            pressed: None,
            hovered_dial: None,
            pending: VecDeque::new(),
            commands,
            interactive: true,
            native_configured: false,
            native_alpha: 255,
            native_extent: [0, 0],
        }));
        let weak = Arc::downgrade(&state);
        let id = viewport_id(&adapter, &device);
        std::thread::spawn(move || {
            let mut after = 0;
            let mut endpoint = (String::new(), String::new());
            loop {
                let Some(state) = weak.upgrade() else { break };
                let visible = state.lock().map(|s| s.visible).unwrap_or(false);
                drop(state);
                if !visible {
                    std::thread::sleep(Duration::from_millis(100));
                    continue;
                }
                let (url, token) = status
                    .lock()
                    .map(|s| (s.endpoint.clone(), s.token.clone()))
                    .unwrap_or_default();
                if url.is_empty() {
                    std::thread::sleep(Duration::from_millis(100));
                    continue;
                }
                if endpoint != (url.clone(), token.clone()) {
                    after = 0;
                    endpoint = (url.clone(), token.clone());
                }
                match http::request(
                    &url,
                    &token,
                    Some((
                        "virtual-frame",
                        &json!({"adapterId":adapter,"deviceId":device,"after":after}),
                    )),
                ) {
                    Ok(value) => {
                        let value = &value["result"];
                        if let (Some(sequence), Some(encoded)) =
                            (value["sequence"].as_u64(), value["jpeg"].as_str())
                        {
                            let decoded = STANDARD
                                .decode(encoded)
                                .map_err(|e| e.to_string())
                                .and_then(|bytes| {
                                    image::load_from_memory(&bytes).map_err(|e| e.to_string())
                                });
                            match decoded {
                                Ok(image) => {
                                    let image = match value["rotation"]
                                        .as_i64()
                                        .unwrap_or(0)
                                        .rem_euclid(360)
                                    {
                                        90 => image.rotate270(),
                                        180 => image.rotate180(),
                                        270 => image.rotate90(),
                                        _ => image,
                                    };
                                    let image = image.to_rgba8();
                                    let size = [image.width() as usize, image.height() as usize];
                                    if size[0] * size[1] <= 16_777_216 {
                                        if let Ok(mut latest) = latest.lock() {
                                            latest.pixels =
                                                Some(egui::ColorImage::from_rgba_unmultiplied(
                                                    size,
                                                    image.as_raw(),
                                                ));
                                            latest.layout = value["layout"].clone();
                                            latest.model =
                                                value["modelId"].as_str().unwrap_or("").into();
                                            latest.error.clear();
                                            after = sequence;
                                        }
                                        context.request_repaint_of(id);
                                    }
                                }
                                Err(error) => {
                                    if let Ok(mut latest) = latest.lock() {
                                        latest.error = error;
                                    }
                                }
                            }
                        }
                    }
                    Err(error) => {
                        if let Ok(mut latest) = latest.lock() {
                            latest.error = error;
                        }
                        std::thread::sleep(Duration::from_millis(150));
                    }
                }
                std::thread::sleep(Duration::from_millis(16));
            }
        });
        state
    }
    fn event(&mut self, event: Value) {
        self.pending
            .push_back(json!({"adapterId":self.adapter,"deviceId":self.device,"input":event}));
    }
    pub fn flush(&mut self) {
        while let Some(params) = self.pending.pop_front() {
            match self
                .commands
                .try_send(Command::Api("virtual-input", params))
            {
                Ok(()) => {}
                Err(TrySendError::Full(Command::Api(_, params))) => {
                    self.pending.push_front(params);
                    break;
                }
                Err(_) => {
                    self.pending.clear();
                    break;
                }
            }
        }
    }
    pub fn hide(&mut self) {
        if let Some(Press::Key(index)) = self.pressed.take() {
            self.event(json!({"type":"key","index":index,"state":"up"}));
        }
        self.visible = false;
        self.native_configured = false;
        self.flush();
    }
    pub fn show_viewport(state: Arc<Mutex<Self>>, context: &egui::Context) {
        let (id, title, size, visible) = {
            let Ok(mut state) = state.lock() else { return };
            state.flush();
            (
                viewport_id(&state.adapter, &state.device),
                state.native_title(),
                state.window_size(),
                state.visible,
            )
        };
        if !visible {
            return;
        }
        context.show_viewport_deferred(
            id,
            egui::ViewportBuilder::default()
                .with_title(title)
                .with_inner_size(size)
                .with_decorations(false)
                .with_transparent(true)
                .with_window_level(egui::WindowLevel::AlwaysOnTop)
                .with_visible(true)
                .with_active(false)
                .with_resizable(false),
            move |context, _| {
                let Ok(mut state) = state.lock() else { return };
                if context.input(|i| i.viewport().close_requested()) {
                    state.hide();
                    return;
                }
                egui::CentralPanel::default()
                    .frame(egui::Frame::NONE)
                    .show(context, |ui| state.paint(ui));
            },
        );
    }
    fn native_title(&self) -> String {
        format!("Hellgato Virtual · {}:{}", self.adapter, self.device)
    }
    fn window_size(&self) -> Vec2 {
        vec2(884.0 * self.scale, 716.0 * self.scale)
    }
    pub fn set_preferred_scale(&mut self, scale: f32, context: &egui::Context) {
        if self.preferred_scale != scale {
            self.preferred_scale = scale;
            self.scale = scale;
            self.native_configured = false;
            context.send_viewport_cmd_to(
                viewport_id(&self.adapter, &self.device),
                egui::ViewportCommand::InnerSize(self.window_size()),
            );
        }
    }
    pub fn paint(&mut self, ui: &mut egui::Ui) {
        let context = ui.ctx().clone();
        let extent = ui.max_rect().size() * context.pixels_per_point();
        let native_extent = [extent.x.round() as u32, extent.y.round() as u32];
        if self.interactive && (!self.native_configured || self.native_extent != native_extent) {
            self.native_configured = window::configure(
                &self.native_title(),
                42.0 * self.scale * context.pixels_per_point(),
            );
            self.native_extent = native_extent;
            self.native_alpha = 255;
        }
        if let Ok(mut latest) = self.latest.lock() {
            if let Some(pixels) = latest.pixels.take() {
                if let Some(texture) = &mut self.texture {
                    texture.set(pixels, egui::TextureOptions::LINEAR);
                } else {
                    self.texture = Some(context.load_texture(
                        "virtual-device-frame",
                        pixels,
                        egui::TextureOptions::LINEAR,
                    ));
                }
                self.layout = latest.layout.clone();
                self.model = latest.model.clone();
            }
        }
        let area = ui.max_rect();
        let width = self.layout["width"].as_f64().unwrap_or(800.0) as f32;
        let height = self.layout["height"].as_f64().unwrap_or(480.0) as f32;
        let draw_scale = self.scale * 800.0 / width;
        let hovered = context.input(|i| i.pointer.hover_pos().is_some_and(|p| area.contains(p)));
        let hover = context.animate_bool_with_time(
            egui::Id::new(("virtual-hover", &self.device)),
            hovered || self.pressed.is_some(),
            0.16,
        );
        let alpha = if self.pressed.is_some() {
            255
        } else {
            ((0.82 + hover * 0.18) * 255.0).round() as u8
        };
        if self.interactive
            && alpha != self.native_alpha
            && window::opacity(&self.native_title(), alpha)
        {
            self.native_alpha = alpha;
        }
        let transform = Projection {
            origin: area.min + vec2(42.0 * draw_scale, 72.0 * draw_scale),
            scale: draw_scale,
        };
        let painter = ui.painter();
        let body = Rect::from_min_size(pos2(-42.0, -72.0), vec2(width + 84.0, height + 236.0));
        surface(
            painter,
            &transform,
            body,
            42.0,
            0.0,
            None,
            Color32::from_rgb(69, 76, 84),
        );
        let rim = rounded_points(body.shrink(3.0), 40.0)
            .into_iter()
            .map(|p| transform.project(p, 0.0))
            .collect();
        painter.add(egui::Shape::closed_line(
            rim,
            Stroke::new(1.0, Color32::from_rgb(110, 119, 128)),
        ));
        painter.text(
            transform.project(pos2(width / 2.0, -38.0), 0.0),
            egui::Align2::CENTER_CENTER,
            "Stream Dog",
            egui::FontId::proportional(15.0 * draw_scale),
            Color32::from_gray(170),
        );
        surface(
            painter,
            &transform,
            Rect::from_min_size(pos2(-22.0, height + 24.0), vec2(width + 44.0, 120.0)),
            18.0,
            0.0,
            None,
            Color32::from_rgb(55, 61, 68),
        );
        let keys: Vec<Rect> = self.layout["keys"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|key| rect_value(&key["rect"]))
            .collect();
        let texture = self.texture.as_ref().map(|t| t.id());
        for (index, rect) in keys.iter().enumerate() {
            let pressed = matches!(self.pressed,Some(Press::Key(key)) if key==index);
            let depth = 0.0;
            surface(
                painter,
                &transform,
                rect.expand(8.0),
                13.0,
                0.0,
                None,
                Color32::from_rgb(22, 25, 29),
            );
            surface(
                painter,
                &transform,
                *rect,
                8.0,
                depth,
                None,
                Color32::from_gray(45),
            );
            if let Some(texture) = texture {
                surface(
                    painter,
                    &transform,
                    *rect,
                    8.0,
                    depth,
                    Some((texture, vec2(width, height))),
                    Color32::WHITE.gamma_multiply(if pressed { 0.68 } else { 1.0 }),
                );
            }
            if pressed {
                let polygon = rounded_points(rect.shrink(if pressed { 3.0 } else { 0.5 }), 7.0)
                    .into_iter()
                    .map(|p| transform.project(p, depth + 0.1))
                    .collect();
                painter.add(egui::Shape::closed_line(
                    polygon,
                    Stroke::new(
                        if pressed { 1.5 } else { 0.6 },
                        Color32::from_white_alpha(if pressed { 150 } else { 40 }),
                    ),
                ));
            }
        }
        let strip = self.layout.get("strip").and_then(|s| {
            Some(Rect::from_min_size(
                pos2(s["x"].as_f64()? as f32, s["y"].as_f64()? as f32),
                vec2(s["width"].as_f64()? as f32, s["height"].as_f64()? as f32),
            ))
        });
        if let Some(rect) = strip {
            surface(
                painter,
                &transform,
                rect.expand(12.0),
                13.0,
                0.0,
                None,
                Color32::from_rgb(22, 25, 29),
            );
            surface(
                painter,
                &transform,
                rect,
                7.0,
                0.5,
                None,
                Color32::from_gray(45),
            );
            if let Some(texture) = texture {
                surface(
                    painter,
                    &transform,
                    rect,
                    7.0,
                    0.6,
                    Some((texture, vec2(width, height))),
                    Color32::WHITE,
                );
            }
        }
        let dials = self.dials(width, height);
        let hover_pointer = context.input(|i| i.pointer.hover_pos());
        for (index, point) in dials.iter().enumerate() {
            let center = transform.project(*point, 10.0);
            let radius = 25.0 * draw_scale;
            painter.circle_filled(center, radius + 3.0, Color32::from_gray(26));
            let cap_color = if matches!(self.pressed,Some(Press::Dial{index:active,..}) if active==index)
            {
                Color32::from_rgb(139, 151, 163)
            } else if hover_pointer.is_some_and(|pointer| pointer.distance(center) <= radius) {
                Color32::from_rgb(192, 201, 210)
            } else {
                Color32::from_rgb(171, 179, 186)
            };
            painter.circle_filled(center, radius, cap_color);
            painter.circle_stroke(
                center,
                radius,
                Stroke::new(2.0 * draw_scale, Color32::from_rgb(218, 224, 229)),
            );
            let angle = match self.pressed {
                Some(Press::Dial {
                    index: active,
                    angle,
                    ..
                }) if active == index => angle,
                _ => -std::f32::consts::FRAC_PI_2,
            };
            let direction = vec2(angle.cos(), angle.sin());
            painter.line_segment(
                [
                    center + direction * radius * 0.5,
                    center + direction * radius * 0.82,
                ],
                Stroke::new(2.0 * draw_scale, Color32::from_rgb(70, 77, 83)),
            );
            if let Some(Press::Dial { index: active, .. }) = &self.pressed {
                if *active == index {
                    draw_gauge(painter, center, (radius * 1.9).max(24.0), angle);
                }
            }
        }
        self.hovered_dial = self
            .hovered_dial
            .filter(|index| {
                dials.get(*index).is_some_and(|point| {
                    let center = transform.project(*point, 0.0);
                    hover_pointer
                        .is_some_and(|pointer| dial_zone(center, draw_scale).contains(pointer))
                })
            })
            .or_else(|| {
                hover_pointer.and_then(|pointer| {
                    dials.iter().position(|point| {
                        transform.project(*point, 0.0).distance(pointer)
                            < (25.0 * draw_scale).max(10.0) + 4.0
                    })
                })
            });
        let active_dial = match self.pressed {
            Some(Press::Arrow { index, .. }) => Some(index),
            _ => self.hovered_dial,
        };
        let arrows = active_dial
            .filter(|_| !matches!(self.pressed, Some(Press::Dial { .. })))
            .map(|index| {
                let center = transform.project(dials[index], 0.0);
                let bounds = dial_arrows(center, draw_scale);
                for (side, rect) in bounds.iter().enumerate() {
                    let points = if side == 0 {
                        vec![
                            pos2(rect.min.x, rect.center().y),
                            rect.right_top(),
                            rect.right_bottom(),
                        ]
                    } else {
                        vec![
                            pos2(rect.max.x, rect.center().y),
                            rect.left_bottom(),
                            rect.left_top(),
                        ]
                    };
                    let pressed=matches!(self.pressed,Some(Press::Arrow{index:active,side:active_side}) if active==index && side==active_side);
                    let hovered=hover_pointer.is_some_and(|point|dial_arrow_contains(*rect,side,point));
                    let color=if pressed{Color32::from_rgb(137,149,160)}else if hovered{Color32::from_rgb(99,111,123)}else{Color32::from_rgb(71,80,90)};
                    painter.add(egui::Shape::convex_polygon(rounded_triangle(&points,2.5),color,Stroke::NONE));
                    let label = pos2(
                        rect.min.x + rect.width() * if side == 0 { 0.66 } else { 0.34 },
                        rect.center().y,
                    );
                    let label=label+vec2(0.0,if pressed{0.5}else{0.0});
                    let stroke=Stroke::new(1.5,Color32::from_gray(245));
                    painter.line_segment([label-vec2(3.0,0.0),label+vec2(3.0,0.0)],stroke);
                    if side==1{painter.line_segment([label-vec2(0.0,3.0),label+vec2(0.0,3.0)],stroke);}
                }
                (index, bounds)
            });
        let pointer = if self.interactive && self.pressed.is_some() {
            window::pointer(&self.native_title(), context.pixels_per_point())
                .map(|p| pos2(p[0], p[1]))
        } else {
            None
        }
        .or_else(|| context.input(|i| i.pointer.interact_pos()));
        let down = context.input(|i| i.pointer.primary_pressed());
        let cursor = match self.pressed {
            Some(Press::Move(_)) | Some(Press::Dial { .. }) => egui::CursorIcon::Grabbing,
            Some(Press::Arrow { .. }) | Some(Press::Key(_)) => egui::CursorIcon::PointingHand,
            _ => pointer
                .map(|pointer| {
                    let point = transform.inverse(pointer);
                    if arrows.is_some_and(|(_, bounds)| {
                        bounds
                            .iter()
                            .enumerate()
                            .any(|(side, rect)| dial_arrow_contains(*rect, side, pointer))
                    }) || keys.iter().any(|rect| rect.contains(point))
                        || strip.is_some_and(|rect| rect.contains(point))
                    {
                        egui::CursorIcon::PointingHand
                    } else if dials.iter().any(|center| center.distance(point) < 35.0)
                        || body.contains(point)
                    {
                        egui::CursorIcon::Grab
                    } else {
                        egui::CursorIcon::Default
                    }
                })
                .unwrap_or(egui::CursorIcon::Default),
        };
        context.set_cursor_icon(cursor);
        let up = self.interactive
            && self.pressed.is_some()
            && if context.input(|i| i.focused) {
                context.input(|i| i.pointer.primary_released() || !i.pointer.primary_down())
            } else {
                window::button_released()
            };
        if down && self.pressed.is_none() {
            if let Some(pointer) = pointer {
                let point = transform.inverse(pointer);
                if let Some((index, bounds)) = arrows.filter(|(_, bounds)| {
                    bounds
                        .iter()
                        .enumerate()
                        .any(|(side, rect)| dial_arrow_contains(*rect, side, pointer))
                }) {
                    let delta = if dial_arrow_contains(bounds[0], 0, pointer) {
                        -1
                    } else {
                        1
                    };
                    self.pressed = Some(Press::Arrow {
                        index,
                        side: if delta < 0 { 0 } else { 1 },
                    });
                    self.event(json!({"type":"dial","kind":"rotate","index":index,"delta":delta}));
                } else if let Some(index) = keys.iter().position(|rect| rect.contains(point)) {
                    self.pressed = Some(Press::Key(index));
                    self.event(json!({"type":"key","index":index,"state":"down"}));
                } else if let Some(index) = dials
                    .iter()
                    .position(|center| center.distance(point) < 35.0)
                {
                    let center = transform.project(dials[index], 0.0);
                    let angle = if pointer.distance(center) >= 8.0 * draw_scale {
                        snap_dial_angle((pointer.y - center.y).atan2(pointer.x - center.x))
                    } else {
                        -std::f32::consts::FRAC_PI_2
                    };
                    self.pressed = Some(Press::Dial {
                        index,
                        previous: (pointer.distance(center) >= 8.0 * draw_scale).then_some(angle),
                        angle,
                        moved: false,
                    });
                } else if strip.is_some_and(|rect| rect.contains(point)) {
                    self.pressed = Some(Press::Touch {
                        start: point,
                        at: Instant::now(),
                    });
                } else if body.contains(point) {
                    if self.interactive {
                        self.pressed = window::Drag::begin(&self.native_title()).map(Press::Move);
                    }
                }
            }
        }
        if let (
            Some(Press::Dial {
                index,
                previous,
                angle,
                moved,
            }),
            Some(pointer),
        ) = (&mut self.pressed, pointer)
        {
            let center = transform.project(dials[*index], 0.0);
            if pointer.distance(center) < 8.0 * draw_scale {
                *previous = None;
            } else {
                let snapped = snap_dial_angle((pointer.y - center.y).atan2(pointer.x - center.x));
                let ticks = previous
                    .map(|last| {
                        (wrapped_angle(snapped - last) / (std::f32::consts::TAU / 8.0)).round()
                            as i32
                    })
                    .unwrap_or(0);
                *previous = Some(snapped);
                *angle = snapped;
                if ticks != 0 {
                    *moved = true;
                    let index = *index;
                    self.event(json!({"type":"dial","kind":"rotate","index":index,"delta":ticks}));
                }
            }
        }
        if let Some(Press::Move(drag)) = &self.pressed {
            drag.update();
        }
        if up {
            if let Some(press) = self.pressed.take() {
                match press {
                    Press::Key(index) => {
                        self.event(json!({"type":"key","index":index,"state":"up"}))
                    }
                    Press::Dial {
                        index,
                        moved: false,
                        ..
                    } => self.event(json!({"type":"dial","kind":"pulse","index":index})),
                    Press::Dial { .. } | Press::Arrow { .. } | Press::Move(_) => {}
                    Press::Touch { start, at } => {
                        if let Some(rect) = strip {
                            let end = pointer.map(|p| transform.inverse(p)).unwrap_or(start);
                            let logical = &self.layout["strip"];
                            let map = |p: Pos2| {
                                [
                                    ((p.x - rect.min.x) / rect.width()
                                        * logical["logicalWidth"].as_f64().unwrap_or(800.0) as f32)
                                        .clamp(
                                            0.0,
                                            logical["logicalWidth"].as_f64().unwrap_or(800.0)
                                                as f32
                                                - 1.0,
                                        ) as u32,
                                    ((p.y - rect.min.y) / rect.height()
                                        * logical["logicalHeight"].as_f64().unwrap_or(100.0) as f32)
                                        .clamp(
                                            0.0,
                                            logical["logicalHeight"].as_f64().unwrap_or(100.0)
                                                as f32
                                                - 1.0,
                                        ) as u32,
                                ]
                            };
                            let a = map(start);
                            let b = map(end);
                            let gesture = if start.distance(end) > 18.0 {
                                "swipe"
                            } else if at.elapsed() > Duration::from_millis(500) {
                                "hold"
                            } else {
                                "tap"
                            };
                            let mut event =
                                json!({"type":"touch","gesture":gesture,"x":a[0],"y":a[1]});
                            if gesture == "swipe" {
                                event["endX"] = json!(b[0]);
                                event["endY"] = json!(b[1]);
                            }
                            self.event(event);
                        }
                    }
                }
            }
        }
        self.flush();
        if self.texture.is_none() {
            painter.text(
                transform.project(pos2(width / 2.0, height / 2.0), 0.0),
                egui::Align2::CENTER_CENTER,
                "Stream Deck 화면 대기 중",
                egui::FontId::proportional(13.0),
                Color32::GRAY,
            );
        }
        if self.pressed.is_some() || !self.pending.is_empty() {
            context.request_repaint_after(Duration::from_millis(16));
        }
    }
    fn dials(&self, width: f32, height: f32) -> Vec<Pos2> {
        if self.model == "n4-pro" {
            [104.0, 302.0, 500.0, 698.0]
                .into_iter()
                .map(|x| pos2(x, 545.0))
                .collect()
        } else {
            let count = self.layout["encoderCount"].as_u64().unwrap_or(0) as usize;
            (0..count)
                .map(|i| pos2(width * (i + 1) as f32 / (count + 1) as f32, height + 64.0))
                .collect()
        }
    }
    pub fn fixture(context: &egui::Context, scenario: &str) -> Self {
        let (commands, _) = std::sync::mpsc::sync_channel(16);
        let keys:Vec<_>=(0..10).map(|index|json!({"id":format!("key:{index}"),"rect":[7+index%5*169,5+index/5*174,112,112]})).collect();
        let mut pixels = egui::ColorImage::filled([800, 480], Color32::from_gray(42));
        for index in 0..10 {
            for y in (5 + index / 5 * 174)..(117 + index / 5 * 174) {
                for x in (7 + index % 5 * 169)..(119 + index % 5 * 169) {
                    pixels[(x, y)] = if index % 2 == 0 {
                        Color32::from_rgb(42, 121, 172)
                    } else {
                        Color32::from_rgb(45, 139, 112)
                    };
                }
            }
        }
        for y in 353..465 {
            for x in 0..800 {
                pixels[(x, y)] = Color32::from_rgb(45, 81, 100);
            }
        }
        Self {
            visible: true,
            adapter: "mirabox".into(),
            device: "native-qa".into(),
            scale: 0.5,
            preferred_scale: 0.5,
            latest: Arc::new(Mutex::new(LatestFrame::default())),
            texture: Some(context.load_texture(
                "virtual-fixture",
                pixels,
                egui::TextureOptions::LINEAR,
            )),
            layout: json!({"width":800,"height":480,"encoderCount":4,"keys":keys,"strip":{"x":0,"y":353,"width":800,"height":112,"logicalWidth":800,"logicalHeight":100}}),
            model: "n4-pro".into(),
            pressed: (scenario == "virtual-knob-pressed")
                .then_some(Press::Arrow { index: 1, side: 1 }),
            hovered_dial: scenario.starts_with("virtual-knob").then_some(1),
            pending: VecDeque::new(),
            commands,
            interactive: false,
            native_configured: false,
            native_alpha: 255,
            native_extent: [0, 0],
        }
    }
}
fn viewport_id(adapter: &str, device: &str) -> egui::ViewportId {
    egui::ViewportId::from_hash_of(("virtual-device", adapter, device))
}
fn rect_value(value: &Value) -> Option<Rect> {
    Some(Rect::from_min_size(
        pos2(value[0].as_f64()? as f32, value[1].as_f64()? as f32),
        vec2(value[2].as_f64()? as f32, value[3].as_f64()? as f32),
    ))
}

#[derive(Clone, Copy)]
struct Projection {
    origin: Pos2,
    scale: f32,
}
impl Projection {
    fn project(&self, p: Pos2, _z: f32) -> Pos2 {
        self.origin + p.to_vec2() * self.scale
    }
    fn inverse(&self, p: Pos2) -> Pos2 {
        pos2(
            (p.x - self.origin.x) / self.scale,
            (p.y - self.origin.y) / self.scale,
        )
    }
}
fn rounded_points(rect: Rect, radius: f32) -> Vec<Pos2> {
    let r = radius.min(rect.width() / 2.0).min(rect.height() / 2.0);
    let mut points = Vec::new();
    for (center, start) in [
        (
            pos2(rect.right() - r, rect.top() + r),
            -std::f32::consts::FRAC_PI_2,
        ),
        (pos2(rect.right() - r, rect.bottom() - r), 0.0),
        (
            pos2(rect.left() + r, rect.bottom() - r),
            std::f32::consts::FRAC_PI_2,
        ),
        (pos2(rect.left() + r, rect.top() + r), std::f32::consts::PI),
    ] {
        for i in 0..=5 {
            let a = start + i as f32 * std::f32::consts::FRAC_PI_2 / 5.0;
            points.push(center + vec2(a.cos(), a.sin()) * r);
        }
    }
    points
}
fn surface(
    painter: &egui::Painter,
    projection: &Projection,
    rect: Rect,
    radius: f32,
    z: f32,
    texture: Option<(egui::TextureId, Vec2)>,
    color: Color32,
) {
    let points = rounded_points(rect, radius);
    let mut mesh = egui::Mesh::default();
    if let Some((texture, _)) = texture {
        mesh.texture_id = texture;
    }
    for p in std::iter::once(rect.center()).chain(points.iter().copied()) {
        let uv = texture
            .map(|(_, size)| pos2(p.x / size.x, p.y / size.y))
            .unwrap_or(egui::epaint::WHITE_UV);
        mesh.vertices.push(egui::epaint::Vertex {
            pos: projection.project(p, z),
            uv,
            color,
        });
    }
    for i in 0..points.len() {
        mesh.indices
            .extend([0, (i + 1) as u32, ((i + 1) % points.len() + 1) as u32]);
    }
    painter.add(egui::Shape::mesh(mesh));
}
fn dial_arrow_contains(rect: Rect, side: usize, point: Pos2) -> bool {
    if !rect.contains(point) {
        return false;
    }
    let inset = (point.y - rect.center().y).abs() / (rect.height() / 2.0) * rect.width();
    if side == 0 {
        point.x >= rect.min.x + inset
    } else {
        point.x <= rect.max.x - inset
    }
}
fn rounded_triangle(points: &[Pos2], radius: f32) -> Vec<Pos2> {
    let mut result = Vec::new();
    for i in 0..3 {
        let corner = points[i];
        let a = corner + (points[(i + 2) % 3] - corner).normalized() * radius;
        let b = corner + (points[(i + 1) % 3] - corner).normalized() * radius;
        for sample in 0..=4 {
            let t = sample as f32 / 4.0;
            result.push(pos2(
                (1.0 - t).powi(2) * a.x + 2.0 * (1.0 - t) * t * corner.x + t * t * b.x,
                (1.0 - t).powi(2) * a.y + 2.0 * (1.0 - t) * t * corner.y + t * t * b.y,
            ));
        }
    }
    result
}
fn dial_zone(center: Pos2, scale: f32) -> Rect {
    let arrows = dial_arrows(center, scale);
    arrows[0].union(arrows[1])
}
fn snap_dial_angle(angle: f32) -> f32 {
    let step = std::f32::consts::TAU / 8.0;
    (wrapped_angle(angle) / step).round() * step
}
fn wrapped_angle(value: f32) -> f32 {
    (value + std::f32::consts::PI).rem_euclid(std::f32::consts::TAU) - std::f32::consts::PI
}
fn draw_gauge(painter: &egui::Painter, center: Pos2, radius: f32, angle: f32) {
    let color = Color32::from_rgba_unmultiplied(0, 205, 245, 210);
    painter.circle_filled(
        center,
        radius,
        Color32::from_rgba_unmultiplied(0, 22, 30, 70),
    );
    painter.circle_stroke(center, radius, Stroke::new(1.0, color));
    for tick in 0..8 {
        let a = -std::f32::consts::FRAC_PI_2 + tick as f32 * std::f32::consts::TAU / 8.0;
        let direction = vec2(a.cos(), a.sin());
        painter.line_segment(
            [
                center + direction * radius * 0.80,
                center + direction * radius,
            ],
            Stroke::new(1.0, color),
        );
    }
    let direction = vec2(angle.cos(), angle.sin());
    painter.line_segment(
        [
            center + direction * radius * 0.45,
            center + direction * radius * 1.08,
        ],
        Stroke::new(2.0, color),
    );
    painter.circle_filled(center + direction * radius * 0.60, 2.5, Color32::WHITE);
}
fn dial_arrows(center: Pos2, scale: f32) -> [Rect; 2] {
    let width = (22.0 * (scale / 0.5).sqrt()).clamp(18.0, 30.0);
    let height = (24.0 * (scale / 0.5).sqrt()).clamp(20.0, 32.0);
    let radius = 25.0 * scale + 5.0;
    [
        Rect::from_min_size(
            center + vec2(-radius - width, -height / 2.0),
            vec2(width, height),
        ),
        Rect::from_min_size(center + vec2(radius, -height / 2.0), vec2(width, height)),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn projection_round_trip_keeps_native_hit_targets_aligned() {
        for scale in [0.25, 0.5, 1.5] {
            let p = Projection {
                origin: pos2(48.0, 48.0),
                scale,
            };
            for point in [pos2(7.0, 5.0), pos2(739.0, 291.0), pos2(698.0, 545.0)] {
                assert!(p.inverse(p.project(point, 0.0)).distance(point) < 0.01);
            }
        }
    }
    #[test]
    fn preferred_size_defaults_to_half_and_resizes_without_changing_aspect() {
        let context = egui::Context::default();
        let mut device = VirtualDevice::fixture(&context, "virtual-2d");
        assert_eq!(device.scale, 0.5);
        let half = device.window_size();
        device.set_preferred_scale(1.0, &context);
        let full = device.window_size();
        assert_eq!(full, half * 2.0);
    }
    #[test]
    fn infinite_dial_crosses_the_angle_boundary_without_a_large_jump() {
        assert!((wrapped_angle((-358.0f32).to_radians()) - 2.0f32.to_radians()).abs() < 0.0001);
        assert!((wrapped_angle(358.0f32.to_radians()) + 2.0f32.to_radians()).abs() < 0.0001);
    }
    #[test]
    fn dial_indicator_uses_the_same_fixed_grid_for_every_mouse_position() {
        for (pointer, expected) in [
            (31.0f32, 45.0f32),
            (52.0, 45.0),
            (350.0, 0.0),
            (-101.0, -90.0),
        ] {
            assert!((snap_dial_angle(pointer.to_radians()) - expected.to_radians()).abs() < 0.0001);
        }
    }
    #[test]
    fn native_pointer_releases_keys_and_distinguishes_dial_click_from_rotation() {
        let context = egui::Context::default();
        let mut device = VirtualDevice::fixture(&context, "virtual-2d");
        let (commands, events) = std::sync::mpsc::sync_channel(16);
        device.commands = commands;
        device.interactive = true;
        let mut draw = |point: Pos2, pressed: bool| {
            let input = egui::RawInput {
                screen_rect: Some(Rect::from_min_size(Pos2::ZERO, vec2(720.0, 546.0))),
                focused: true,
                events: vec![
                    egui::Event::PointerMoved(point),
                    egui::Event::PointerButton {
                        pos: point,
                        button: egui::PointerButton::Primary,
                        pressed,
                        modifiers: egui::Modifiers::NONE,
                    },
                ],
                ..Default::default()
            };
            let mut output = context.run_ui(input, |ui| {
                egui::CentralPanel::default()
                    .frame(egui::Frame::NONE)
                    .show(ui, |ui| device.paint(ui));
            });
            let observation = (
                output.platform_output.cursor_icon,
                match device.pressed {
                    Some(Press::Dial { angle, .. }) => Some(angle),
                    _ => None,
                },
            );
            output.textures_delta.clear();
            observation
        };
        draw(pos2(40.0, 60.0), true);
        draw(pos2(10.0, 530.0), false);
        let key: Vec<_> = events
            .try_iter()
            .map(|command| match command {
                Command::Api(_, value) => value["input"].clone(),
                _ => panic!("unexpected native command"),
            })
            .collect();
        assert_eq!(
            key,
            vec![
                json!({"type":"key","index":0,"state":"down"}),
                json!({"type":"key","index":0,"state":"up"})
            ]
        );
        let center = pos2(42.0 * 0.5 + 104.0 * 0.5, 72.0 * 0.5 + 545.0 * 0.5);
        draw(center, true);
        draw(center, false);
        match events.try_recv().unwrap() {
            Command::Api(_, value) => assert_eq!(
                value["input"],
                json!({"type":"dial","kind":"pulse","index":0})
            ),
            _ => panic!("unexpected native command"),
        }
        draw(center - vec2(30.0, 0.0), true);
        draw(center - vec2(30.0, 0.0), false);
        draw(center + vec2(30.0, 0.0), true);
        draw(center + vec2(30.0, 0.0), false);
        draw(center + vec2(12.0, 0.0), true);
        let before_tick = draw(
            center
                + vec2(
                    12.0 * 20.0f32.to_radians().cos(),
                    12.0 * 20.0f32.to_radians().sin(),
                ),
            true,
        );
        assert_eq!(before_tick.0, egui::CursorIcon::Grabbing);
        assert_eq!(before_tick.1, Some(0.0));
        draw(center + vec2(0.0, 24.0), true);
        draw(center + vec2(0.0, 24.0), false);
        let rotations: Vec<_> = events
            .try_iter()
            .map(|event| match event {
                Command::Api(_, value) => {
                    assert_eq!(value["input"]["kind"], "rotate");
                    value["input"]["delta"].as_i64().unwrap()
                }
                _ => panic!("unexpected native command"),
            })
            .collect();
        assert_eq!(&rotations[..2], &[-1, 1]);
        assert_eq!(rotations[2..].iter().sum::<i64>(), 2);
    }
}
