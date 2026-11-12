use eframe::egui::{self, pos2, vec2, Color32, FontId, Rect, Sense, Stroke};

pub const SHELL: Color32 = Color32::from_rgb(0x53, 0x53, 0x53);
pub const DIVIDER: Color32 = Color32::from_rgb(0x38, 0x38, 0x38);
pub const TABS: Color32 = Color32::from_rgb(0x42, 0x42, 0x42);
pub const WORKSPACE: Color32 = Color32::from_rgb(0x30, 0x30, 0x30);
pub const FIELD: Color32 = Color32::from_rgb(0x26, 0x26, 0x26);
pub const TEXT: Color32 = Color32::from_rgb(0xe4, 0xe4, 0xe4);
pub const MUTED: Color32 = Color32::from_rgb(0xb0, 0xb0, 0xb0);
pub const SUCCESS: Color32 = Color32::from_rgb(0x99, 0xd0, 0xaa);
pub const ERROR: Color32 = Color32::from_rgb(0xf1, 0xaa, 0xa4);
pub const APP_ICON: &[u8] = include_bytes!("../../../../app/assets/icons/hellgato-app-64.png");
pub const DOG_ICON: &[u8] = include_bytes!("../../../../app/assets/icons/stream-dog-64.png");

#[derive(Clone, Copy)]
pub struct Layout {
    pub frame: Rect,
    pub title: Rect,
    pub toolbar: Rect,
    pub sidebar: Rect,
    pub boundary: Rect,
    pub tabs: Rect,
    pub content: Rect,
    pub status: Rect,
}

impl Layout {
    pub fn new(rect: Rect, maximized: bool) -> Self {
        let frame = if maximized {
            rect
        } else {
            Rect::from_min_max(rect.min + vec2(3.0, 1.0), rect.max - vec2(3.0, 3.0))
        };
        let title = Rect::from_min_size(frame.min, vec2(frame.width(), 34.0));
        let toolbar = Rect::from_min_size(
            pos2(frame.left(), title.bottom() + 1.0),
            vec2(frame.width(), 40.0),
        );
        let status = Rect::from_min_max(pos2(frame.left(), frame.bottom() - 28.0), frame.max);
        let body_top = toolbar.bottom() + 1.0;
        let sidebar = Rect::from_min_max(
            pos2(frame.left(), body_top),
            pos2(frame.left() + 244.0, status.top() - 1.0),
        );
        let boundary = Rect::from_min_max(
            pos2(sidebar.right(), body_top),
            pos2(sidebar.right() + 3.0, sidebar.bottom()),
        );
        let tabs = Rect::from_min_size(
            pos2(boundary.right(), body_top),
            vec2(frame.right() - boundary.right(), 28.0),
        );
        let content = Rect::from_min_max(
            pos2(tabs.left(), tabs.bottom() + 1.0),
            pos2(frame.right(), status.top() - 1.0),
        );
        Self {
            frame,
            title,
            toolbar,
            sidebar,
            boundary,
            tabs,
            content,
            status,
        }
    }

    pub fn paint(self, ui: &egui::Ui) {
        let painter = ui.painter();
        painter.rect_filled(ui.max_rect(), 6, SHELL);
        painter.rect_stroke(
            ui.max_rect(),
            6,
            Stroke::new(1.0, DIVIDER),
            egui::StrokeKind::Inside,
        );
        painter.rect_filled(self.frame, 0, DIVIDER);
        for rect in [self.title, self.toolbar, self.sidebar, self.status] {
            painter.rect_filled(rect, 0, SHELL);
        }
        painter.rect_filled(self.tabs, 0, TABS);
        painter.rect_filled(self.content, 0, WORKSPACE);
        painter.rect_filled(self.boundary, 0, DIVIDER);
        painter.rect_filled(
            Rect::from_min_size(
                self.boundary.min + vec2(1.0, 0.0),
                vec2(1.0, self.boundary.height()),
            ),
            0,
            Color32::from_gray(0x47),
        );
    }
}

pub fn configure(context: &egui::Context) {
    context.set_theme(egui::ThemePreference::Dark);
    let mut style = egui::Style {
        visuals: egui::Visuals::dark(),
        ..Default::default()
    };
    style.visuals.override_text_color = Some(TEXT);
    style.visuals.panel_fill = WORKSPACE;
    style.visuals.window_fill = Color32::from_gray(0x2b);
    style.visuals.widgets.noninteractive.bg_stroke = Stroke::new(1.0, Color32::from_gray(0x44));
    style.visuals.extreme_bg_color = FIELD;
    style.visuals.selection.bg_fill = SHELL;
    style.visuals.selection.stroke = Stroke::new(1.0, TEXT);
    for widget in [
        &mut style.visuals.widgets.inactive,
        &mut style.visuals.widgets.hovered,
        &mut style.visuals.widgets.active,
    ] {
        widget.corner_radius = 2.into();
        widget.bg_stroke = Stroke::new(1.0, DIVIDER);
        widget.fg_stroke = Stroke::new(1.0, TEXT);
    }
    style.visuals.widgets.inactive.weak_bg_fill = TABS;
    style.visuals.widgets.inactive.bg_fill = TABS;
    style.visuals.widgets.hovered.weak_bg_fill = Color32::from_gray(0x62);
    style.visuals.widgets.active.weak_bg_fill = Color32::from_gray(0x6b);
    style.spacing.item_spacing = vec2(8.0, 8.0);
    style.spacing.button_padding = vec2(10.0, 5.0);
    style.spacing.menu_margin = egui::Margin::same(4);
    style.spacing.menu_width = 300.0;
    style.visuals.menu_corner_radius = 3.into();
    style.visuals.popup_shadow = egui::epaint::Shadow {
        offset: [0, 2],
        blur: 4,
        spread: 0,
        color: Color32::from_black_alpha(48),
    };
    style
        .text_styles
        .insert(egui::TextStyle::Body, FontId::proportional(12.0));
    style
        .text_styles
        .insert(egui::TextStyle::Button, FontId::proportional(12.0));
    style
        .text_styles
        .insert(egui::TextStyle::Small, FontId::proportional(10.0));
    style
        .text_styles
        .insert(egui::TextStyle::Heading, FontId::proportional(20.0));
    context.set_style_of(egui::Theme::Dark, style);
    if let Ok(font) = std::fs::read("C:/Windows/Fonts/malgun.ttf") {
        let mut fonts = egui::FontDefinitions::default();
        fonts
            .font_data
            .insert("korean".into(), egui::FontData::from_owned(font).into());
        fonts
            .families
            .entry(egui::FontFamily::Proportional)
            .or_default()
            .insert(0, "korean".into());
        context.set_fonts(fonts);
    }
}

pub fn texture(context: &egui::Context, name: &str, bytes: &[u8]) -> egui::TextureHandle {
    let icon = eframe::icon_data::from_png_bytes(bytes).expect("embedded app icon");
    context.load_texture(
        name,
        egui::ColorImage::from_rgba_unmultiplied(
            [icon.width as usize, icon.height as usize],
            &icon.rgba,
        ),
        egui::TextureOptions::LINEAR,
    )
}

pub fn region<R>(
    ui: &mut egui::Ui,
    id: &str,
    rect: Rect,
    contents: impl FnOnce(&mut egui::Ui) -> R,
) -> R {
    ui.scope_builder(
        egui::UiBuilder::new()
            .id_salt(id)
            .max_rect(rect)
            .layout(egui::Layout::top_down(egui::Align::Min)),
        |ui| {
            ui.set_clip_rect(rect);
            contents(ui)
        },
    )
    .inner
}

pub fn caption(ui: &mut egui::Ui, layout: Layout, maximized: bool, close: &mut bool) {
    let drag = Rect::from_min_max(
        layout.title.min + vec2(230.0, 0.0),
        layout.title.max - vec2(138.0, 0.0),
    );
    let response = ui.interact(drag, ui.id().with("title-drag"), Sense::click_and_drag());
    if response.drag_started() {
        ui.ctx().send_viewport_cmd(egui::ViewportCommand::StartDrag);
    }
    if response.double_clicked() {
        ui.ctx()
            .send_viewport_cmd(egui::ViewportCommand::Maximized(!maximized));
    }
    ui.painter().with_clip_rect(drag).text(
        layout.title.center(),
        egui::Align2::CENTER_CENTER,
        "Hellgato Stream Dog",
        FontId::proportional(12.0),
        TEXT,
    );
    for index in 0..3 {
        let rect = Rect::from_min_size(
            pos2(
                layout.title.right() - (3 - index) as f32 * 46.0,
                layout.title.top(),
            ),
            vec2(46.0, 34.0),
        );
        let response = ui.interact(rect, ui.id().with(("caption", index)), Sense::click());
        let response = response.on_hover_text(match index {
            0 => "최소화",
            1 if maximized => "복원",
            1 => "최대화",
            _ => "닫기",
        });
        if response.hovered() {
            ui.painter().rect_filled(
                rect,
                0,
                if index == 2 {
                    Color32::from_rgb(0xe8, 0x11, 0x23)
                } else {
                    Color32::from_gray(0x68)
                },
            );
        }
        let glyph = Rect::from_center_size(rect.center(), vec2(10.0, 10.0));
        let stroke = Stroke::new(1.0, TEXT);
        match index {
            0 => {
                ui.painter().line_segment(
                    [
                        pos2(glyph.left(), glyph.center().y),
                        pos2(glyph.right(), glyph.center().y),
                    ],
                    stroke,
                );
            }
            1 => {
                if maximized {
                    ui.painter().rect_stroke(
                        glyph.translate(vec2(1.5, -1.5)).shrink(1.5),
                        0,
                        stroke,
                        egui::StrokeKind::Inside,
                    );
                }
                ui.painter().rect_filled(
                    glyph,
                    0,
                    if response.hovered() {
                        Color32::from_gray(0x68)
                    } else {
                        SHELL
                    },
                );
                ui.painter()
                    .rect_stroke(glyph.shrink(0.5), 0, stroke, egui::StrokeKind::Inside);
            }
            _ => {
                ui.painter()
                    .line_segment([glyph.left_top(), glyph.right_bottom()], stroke);
                ui.painter()
                    .line_segment([glyph.right_top(), glyph.left_bottom()], stroke);
            }
        }
        if response.clicked() {
            match index {
                0 => ui
                    .ctx()
                    .send_viewport_cmd(egui::ViewportCommand::Minimized(true)),
                1 => ui
                    .ctx()
                    .send_viewport_cmd(egui::ViewportCommand::Maximized(!maximized)),
                _ => *close = true,
            }
        }
    }
}

pub fn resize_edges(ui: &mut egui::Ui, maximized: bool) {
    if maximized {
        return;
    }
    let rect = ui.max_rect();
    let directions = [
        (
            egui::ResizeDirection::North,
            Rect::from_min_max(
                rect.min + vec2(8.0, 0.0),
                pos2(rect.right() - 8.0, rect.top() + 3.0),
            ),
            egui::CursorIcon::ResizeVertical,
        ),
        (
            egui::ResizeDirection::South,
            Rect::from_min_max(
                pos2(rect.left() + 8.0, rect.bottom() - 3.0),
                rect.max - vec2(8.0, 0.0),
            ),
            egui::CursorIcon::ResizeVertical,
        ),
        (
            egui::ResizeDirection::West,
            Rect::from_min_max(
                rect.min + vec2(0.0, 8.0),
                pos2(rect.left() + 3.0, rect.bottom() - 8.0),
            ),
            egui::CursorIcon::ResizeHorizontal,
        ),
        (
            egui::ResizeDirection::East,
            Rect::from_min_max(
                pos2(rect.right() - 3.0, rect.top() + 8.0),
                rect.max - vec2(0.0, 8.0),
            ),
            egui::CursorIcon::ResizeHorizontal,
        ),
        (
            egui::ResizeDirection::NorthWest,
            Rect::from_min_size(rect.min, vec2(8.0, 8.0)),
            egui::CursorIcon::ResizeNwSe,
        ),
        (
            egui::ResizeDirection::SouthEast,
            Rect::from_min_size(rect.max - vec2(8.0, 8.0), vec2(8.0, 8.0)),
            egui::CursorIcon::ResizeNwSe,
        ),
        (
            egui::ResizeDirection::NorthEast,
            Rect::from_min_size(rect.right_top() - vec2(8.0, 0.0), vec2(8.0, 8.0)),
            egui::CursorIcon::ResizeNeSw,
        ),
        (
            egui::ResizeDirection::SouthWest,
            Rect::from_min_size(rect.left_bottom() - vec2(0.0, 8.0), vec2(8.0, 8.0)),
            egui::CursorIcon::ResizeNeSw,
        ),
    ];
    for (index, (direction, bounds, cursor)) in directions.into_iter().enumerate() {
        let response = ui
            .interact(bounds, ui.id().with(("resize", index)), Sense::drag())
            .on_hover_cursor(cursor);
        if response.drag_started() {
            ui.ctx()
                .send_viewport_cmd(egui::ViewportCommand::BeginResize(direction));
        }
    }
}
