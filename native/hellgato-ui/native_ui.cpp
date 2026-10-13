#include <windows.h>

#include <QApplication>
#include <QComboBox>
#include <QFile>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QLocale>
#include <QMetaMethod>
#include <QMetaProperty>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QPointer>
#include <QPushButton>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSignalBlocker>
#include <QSlider>
#include <QTimer>
#include <QVBoxLayout>

#include <memory>
#include <vector>

namespace {
QJsonObject inspectObject(QObject *object, int depth) {
    QJsonObject entry{{"class", object->metaObject()->className()}, {"name", object->objectName()}};
    const auto *meta = object->metaObject();
    QJsonObject properties;
    for (int index = 0; index < meta->propertyCount(); ++index) {
        const auto property = meta->property(index);
        const auto value = property.read(object);
        if (value.typeId() == QMetaType::QString || value.typeId() == QMetaType::Bool || value.typeId() == QMetaType::Int)
            properties[property.name()] = QJsonValue::fromVariant(value);
    }
    entry["properties"] = properties;
    if (auto *combo = qobject_cast<QComboBox *>(object)) {
        QJsonArray items;
        for (int index = 0; index < combo->count(); ++index) {
            QJsonObject item{{"text", combo->itemText(index)}};
            QJsonObject roles;
            for (int role = Qt::UserRole; role < Qt::UserRole + 12; ++role)
                if (combo->itemData(index, role).isValid()) roles[QString::number(role)] = combo->itemData(index, role).toString();
            item["data"] = roles;
            items.append(item);
        }
        entry["items"] = items;
    }
    if (auto *widget = qobject_cast<QWidget *>(object)) {
        entry["visible"] = widget->isVisible();
        entry["width"] = widget->width();
        entry["height"] = widget->height();
        if (widget->layout()) entry["layout"] = widget->layout()->metaObject()->className();
    }
    if (meta->className()[0] != 'Q') {
        QJsonArray methods;
        for (int index = meta->methodOffset(); index < meta->methodCount(); ++index)
            methods.append(QString::fromLatin1(meta->method(index).methodSignature()));
        entry["methods"] = methods;
    }
    if (depth < 14) {
        QJsonArray children;
        for (auto *child : object->children()) children.append(inspectObject(child, depth + 1));
        entry["children"] = children;
    }
    return entry;
}

void writeDiagnostics(const QString &path) {
    QJsonArray windows;
    for (auto *window : QApplication::topLevelWidgets())
        if (!window->parentWidget()) windows.append(inspectObject(window, 0));
    QSaveFile output(path);
    if (output.open(QIODevice::WriteOnly)) {
        output.write(QJsonDocument(QJsonObject{{"qtVersion", qVersion()}, {"windows", windows}}).toJson());
        output.commit();
    }
}

void copyAppearance(QWidget *target, QWidget *source) {
    target->setFont(source->font());
    target->setPalette(source->palette());
    target->setStyleSheet(source->styleSheet());
    target->setSizePolicy(source->sizePolicy());
    target->setMinimumSize(source->minimumSize());
    target->setMaximumSize(source->maximumSize());
    target->setFocusPolicy(source->focusPolicy());
    target->setCursor(source->cursor());
    target->setMouseTracking(source->hasMouseTracking());
}

struct DeviceControls {
    QPointer<QWidget> page;
    QPointer<QWidget> brightness;
    QPointer<QSlider> slider;
    QPointer<QLabel> label;
    QPointer<QWidget> firmwareButton;
    QSize firmwareMinimum;
    QSize firmwareMaximum;
    bool firmwareCollapsed = false;
    bool editing = false;
    int pendingValue = -1;
    bool pendingPersist = false;
};

class NativeUi : public QObject {
public:
    explicit NativeUi(const QJsonObject &config) : QObject(qApp), config_(config), network_(this) {
        setObjectName("hellgatoNativeBrightnessRowV2");
        qApp->installEventFilter(this);
        auto *timer = new QTimer(this);
        connect(timer, &QTimer::timeout, this, [this] { refresh(); });
        timer->start(150);
        auto *commands = new QTimer(this);
        commands->setTimerType(Qt::PreciseTimer);
        connect(commands, &QTimer::timeout, this, [this] { flushControl(); });
        commands->start(33);
        refresh();
        readState();
    }

    ~NativeUi() override {
        for (const auto &entry : controls_) {
            restoreFirmware(entry.get());
            if (entry->brightness) entry->brightness->deleteLater();
        }
    }

    void configure(const QJsonObject &config) {
        config_ = config;
        state_ = QJsonObject();
        refresh();
        readState();
    }

private:
    QString labelText() const {
        return QLocale().language() == QLocale::Korean ? QString::fromUtf8("노브 밝기:") : QStringLiteral("Knob brightness:");
    }

    QString selectedSerial(QWidget *page) const {
        auto *label = page ? page->findChild<QObject *>("lb_SNValue") : nullptr;
        const auto value = label ? label->property("text") : QVariant();
        return value.typeId() == QMetaType::QString ? value.toString().trimmed() : QString();
    }

    bool selected(QWidget *page) const { return selectedSerial(page) == config_["serial"].toString(); }
    bool ready() const {
        return state_["serial"].toString() == config_["serial"].toString() &&
               state_["available"].toBool() && state_["connected"].toBool();
    }

    bool eventFilter(QObject *object, QEvent *event) override {
        if (object->objectName() != "clb_updateFW" && object->objectName() != "lb_fwVerText") return false;
        auto *widget = qobject_cast<QWidget *>(object);
        auto *page = widget ? widget->parentWidget() : nullptr;
        while (page && !page->findChild<QObject *>("lb_SNValue", Qt::FindDirectChildrenOnly)) {
            if (page->findChild<QWidget *>("serialNumberRow", Qt::FindDirectChildrenOnly)) break;
            page = page->parentWidget();
        }
        if (!selected(page)) return false;
        switch (event->type()) {
        case QEvent::MouseButtonPress:
        case QEvent::MouseButtonRelease:
        case QEvent::MouseButtonDblClick:
        case QEvent::KeyPress:
        case QEvent::KeyRelease:
        case QEvent::Shortcut:
            return true;
        default:
            return false;
        }
    }

    QNetworkRequest request(const QString &path) const {
        QNetworkRequest result(QUrl(config_["endpoint"].toString() + path));
        result.setRawHeader("Authorization", "Bearer " + config_["token"].toString().toUtf8());
        result.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
        result.setTransferTimeout(3000);
        result.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::ManualRedirectPolicy);
        return result;
    }

    void readState() {
        if (statePending_) return;
        statePending_ = true;
        auto *reply = network_.get(request("/state"));
        connect(reply, &QNetworkReply::finished, this, [this, reply] {
            statePending_ = false;
            const auto document = QJsonDocument::fromJson(reply->readAll());
            state_ = reply->error() == QNetworkReply::NoError && document.isObject() ? document.object() : QJsonObject();
            reply->deleteLater();
            syncControls();
        });
    }

    void queueControl(DeviceControls *entry, int value, bool persist) {
        if (!entry->page || !selected(entry->page) || !ready()) return;
        entry->pendingValue = value;
        entry->pendingPersist = persist;
        entry->editing = true;
    }

    void flushControl() {
        if (controlPending_ || !ready()) return;
        for (const auto &controls : controls_) {
            auto *entry = controls.get();
            if (!entry->page || !selected(entry->page) || entry->pendingValue < 0) continue;
            const int value = entry->pendingValue;
            const bool persist = entry->pendingPersist;
            entry->pendingValue = -1;
            controlPending_ = true;
            QJsonObject payload{{"serial", config_["serial"]}, {"action", "knobBrightness"},
                                {"value", value}, {"persist", persist}};
            auto *reply = network_.post(request("/control"), QJsonDocument(payload).toJson(QJsonDocument::Compact));
            connect(reply, &QNetworkReply::finished, this, [this, reply, entry, value, persist] {
                const auto result = QJsonDocument::fromJson(reply->readAll()).object();
                const bool success = reply->error() == QNetworkReply::NoError && result["ok"].toBool();
                controlPending_ = false;
                if (entry->brightness) {
                    entry->brightness->setToolTip(success ? QString() :
                        (QLocale().language() == QLocale::Korean ? QString::fromUtf8("노브 밝기 적용 실패 · Hellgato 상태를 확인하세요")
                                                              : QStringLiteral("Knob brightness could not be applied; check Hellgato")));
                }
                if (entry->slider && !entry->slider->isSliderDown() && entry->pendingValue < 0 &&
                    (!success || (persist && entry->slider->value() == value))) entry->editing = false;
                reply->deleteLater();
                if (persist || !success) readState();
            });
            break;
        }
    }

    void install(QWidget *page, QWidget *sourceRow, QVBoxLayout *parentLayout) {
        auto *sourceLayout = qobject_cast<QHBoxLayout *>(sourceRow->layout());
        auto *sourceSlider = sourceRow->findChild<QSlider *>("brightnessSliderbar");
        auto *sourceLabel = sourceRow->findChild<QLabel *>("lb_text1");
        auto *sourceDarker = sourceRow->findChild<QPushButton *>("iconDarker");
        auto *sourceBrighter = sourceRow->findChild<QPushButton *>("iconBrighter");
        if (!sourceLayout || !sourceSlider || !sourceLabel || !sourceDarker || !sourceBrighter) return;
        auto controls = std::make_unique<DeviceControls>();
        auto *entry = controls.get();
        entry->page = page;
        auto *row = new QWidget(page);
        copyAppearance(row, sourceRow);
        row->setObjectName("hellgatoKnobBrightnessRow");
        row->setFixedHeight(sourceRow->height());
        auto *layout = new QHBoxLayout(row);
        layout->setContentsMargins(sourceLayout->contentsMargins());
        layout->setSpacing(sourceLayout->spacing());
        entry->brightness = row;
        for (int index = 0; index < sourceLayout->count(); ++index) {
            auto *item = sourceLayout->itemAt(index);
            QWidget *widget = nullptr;
            if (auto *source = item->widget()) {
                if (source == sourceLabel) {
                    auto *label = new QLabel(row);
                    copyAppearance(label, source);
                    label->setAlignment(sourceLabel->alignment());
                    label->setMargin(sourceLabel->margin());
                    label->setIndent(sourceLabel->indent());
                    label->setText(labelText());
                    entry->label = label;
                    widget = label;
                } else if (source == sourceSlider) {
                    auto *slider = new QSlider(sourceSlider->orientation(), row);
                    copyAppearance(slider, source);
                    slider->setObjectName("hellgatoKnobBrightness");
                    slider->setRange(sourceSlider->minimum(), sourceSlider->maximum());
                    slider->setSingleStep(sourceSlider->singleStep());
                    slider->setPageStep(sourceSlider->pageStep());
                    slider->setInvertedAppearance(sourceSlider->invertedAppearance());
                    slider->setInvertedControls(sourceSlider->invertedControls());
                    slider->setTracking(true);
                    entry->slider = slider;
                    widget = slider;
                } else if (source == sourceDarker || source == sourceBrighter) {
                    auto *button = new QPushButton(row);
                    auto *prototype = qobject_cast<QPushButton *>(source);
                    copyAppearance(button, source);
                    button->setAutoDefault(prototype->autoDefault());
                    button->setAutoRepeat(prototype->autoRepeat());
                    button->setAutoRepeatDelay(prototype->autoRepeatDelay());
                    button->setAutoRepeatInterval(prototype->autoRepeatInterval());
                    const bool darker = source == sourceDarker;
                    button->setObjectName(darker ? "hellgatoKnobDarker" : "hellgatoKnobBrighter");
                    connect(button, &QPushButton::clicked, this, [entry, darker] {
                        if (entry->slider) entry->slider->triggerAction(darker ? QAbstractSlider::SliderSingleStepSub
                                                                            : QAbstractSlider::SliderSingleStepAdd);
                    });
                    widget = button;
                } else { delete row; return; }
                layout->addWidget(widget, sourceLayout->stretch(index), item->alignment());
            } else if (auto *spacer = item->spacerItem()) {
                layout->addSpacerItem(new QSpacerItem(spacer->sizeHint().width(), spacer->sizeHint().height(),
                    spacer->sizePolicy().horizontalPolicy(), spacer->sizePolicy().verticalPolicy()));
                layout->setStretch(index, sourceLayout->stretch(index));
            } else { delete row; return; }
        }
        if (!entry->slider || !entry->label) { delete row; return; }
        connect(entry->slider, &QSlider::sliderPressed, this, [entry] { entry->editing = true; });
        connect(entry->slider, &QSlider::valueChanged, this, [this, entry](int value) {
            if (!syncing_) queueControl(entry, value, !entry->slider->isSliderDown());
        });
        connect(entry->slider, &QSlider::sliderReleased, this, [this, entry] {
            queueControl(entry, entry->slider->value(), true);
        });
        parentLayout->insertWidget(parentLayout->indexOf(sourceRow) + 1, row);
        entry->firmwareButton = page->findChild<QWidget *>("clb_updateFW");
        controls_.push_back(std::move(controls));
    }

    void restoreFirmware(DeviceControls *entry) {
        if (entry->firmwareButton && entry->firmwareCollapsed) {
            entry->firmwareButton->setMinimumSize(entry->firmwareMinimum);
            entry->firmwareButton->setMaximumSize(entry->firmwareMaximum);
            entry->firmwareCollapsed = false;
        }
    }

    void syncControls() {
        syncing_ = true;
        for (const auto &controls : controls_) {
            auto *entry = controls.get();
            if (!entry->page) continue;
            const bool matching = selected(entry->page);
            entry->brightness->setVisible(matching);
            entry->brightness->setEnabled(matching && ready());
            entry->label->setText(labelText());
            if (ready() && !entry->editing && entry->pendingValue < 0 && !entry->slider->isSliderDown()) {
                QSignalBlocker block(entry->slider);
                entry->slider->setValue(state_["knobBrightness"].toInt(100));
            }
            if (entry->firmwareButton && matching) {
                if (!entry->firmwareCollapsed) {
                    entry->firmwareMinimum = entry->firmwareButton->minimumSize();
                    entry->firmwareMaximum = entry->firmwareButton->maximumSize();
                    entry->firmwareCollapsed = true;
                }
                // Preserve the host's own visibility and enabled state for real devices.
                entry->firmwareButton->setFixedSize(0, 0);
            } else restoreFirmware(entry);
        }
        syncing_ = false;
    }

    void refresh() {
        if (!config_["diagnosticsOnly"].toBool()) {
            for (auto *window : QApplication::topLevelWidgets()) {
                for (auto *row : window->findChildren<QWidget *>("brightnessRow")) {
                    auto *page = row->parentWidget();
                    auto *layout = qobject_cast<QVBoxLayout *>(page ? page->layout() : nullptr);
                    if (!layout || layout->indexOf(row) < 0 || !page->findChild<QObject *>("lb_SNValue")) continue;
                    bool known = false;
                    for (const auto &entry : controls_) if (entry->page == page) { known = true; break; }
                    if (!known && selected(page)) install(page, row, layout);
                }
            }
            syncControls();
            if (++ticks_ >= 6) { ticks_ = 0; readState(); }
        }
        const auto diagnostics = config_["diagnosticsPath"].toString();
        if (!diagnostics.isEmpty() && ++diagnosticTicks_ >= 13) {
            diagnosticTicks_ = 0;
            if (config_["diagnosticsOnly"].toBool()) writeDiagnostics(diagnostics);
            else {
                int count = 0;
                for (const auto &entry : controls_) if (entry->page && selected(entry->page)) ++count;
                QSaveFile output(diagnostics);
                if (output.open(QIODevice::WriteOnly)) {
                    output.write(QJsonDocument(QJsonObject{{"event", "nativeUiReady"}, {"qtVersion", qVersion()},
                        {"serial", config_["serial"]}, {"controls", count}, {"connected", ready()}}).toJson());
                    output.commit();
                }
            }
        }
    }
    QJsonObject config_;
    QJsonObject state_;
    QNetworkAccessManager network_;
    std::vector<std::unique_ptr<DeviceControls>> controls_;
    bool statePending_ = false;
    bool controlPending_ = false;
    bool syncing_ = false;
    int ticks_ = 0;
    int diagnosticTicks_ = 0;
};
}

extern "C" __declspec(dllexport) DWORD WINAPI HellgatoInitialize(void *argument) {
    if (!qApp || !qobject_cast<QApplication *>(qApp)) return 1;
    QFile config(QString::fromWCharArray(static_cast<const wchar_t *>(argument)));
    if (!config.open(QIODevice::ReadOnly)) return 2;
    const auto document = QJsonDocument::fromJson(config.readAll());
    if (!document.isObject()) return 3;
    const auto settings = document.object();
    const QUrl endpoint(settings["endpoint"].toString());
    if (endpoint.scheme() != "http" || endpoint.host() != "127.0.0.1" || endpoint.port() != 5345 ||
        !endpoint.path().isEmpty() || endpoint.hasQuery() || endpoint.hasFragment() || !endpoint.userInfo().isEmpty() ||
        settings["token"].toString().size() < 32 || settings["token"].toString().contains(QRegularExpression("\\s")) ||
        !QRegularExpression("^HGMOCK[0-9]{6}$").match(settings["serial"].toString()).hasMatch()) return 5;
    return QMetaObject::invokeMethod(qApp, [settings] {
        auto *existing = qApp->findChild<QObject *>("hellgatoNativeBrightnessRowV2", Qt::FindDirectChildrenOnly);
        if (existing) static_cast<NativeUi *>(existing)->configure(settings);
        else new NativeUi(settings);
    }, Qt::QueuedConnection) ? 0 : 4;
}

extern "C" __declspec(dllexport) DWORD WINAPI HellgatoShutdown(void *) {
    if (!qApp) return 1;
    return QMetaObject::invokeMethod(qApp, [] {
        if (auto *context = qApp->findChild<QObject *>("hellgatoNativeBrightnessRowV2", Qt::FindDirectChildrenOnly))
            context->deleteLater();
    }, Qt::QueuedConnection) ? 0 : 4;
}

BOOL WINAPI DllMain(HINSTANCE, DWORD, LPVOID) { return TRUE; }
