#include "native_ui.cpp"

#include <QEventLoop>
#include <QTcpServer>
#include <QTcpSocket>
#include <QTextStream>
#include <QAction>
#include <QDialog>
#include <QCheckBox>

class ESDDeviceComboBox : public QWidget {
    Q_OBJECT
public:
    using QWidget::QWidget;
};

class ESDCoraDocksManagerEx : public QObject {
    Q_OBJECT
public:
    using QObject::QObject;
    int requests = 0;
public slots:
    void onRescan() { ++requests; }
};

int reconnectContract(QApplication &app) {
    QJsonObject results;
    ESDCoraDocksManagerEx manager;
    auto flush = [&] { app.processEvents(); app.processEvents(); };
    {
        LocalDockReconnect reconnect(&app, [&] { return &manager; });
        reconnect.poll(false, false, 0); reconnect.poll(true, true, 1000); flush();
        results["noRescanWithoutNeed"] = manager.requests == 0;
        reconnect.poll(true, false, 5000); flush();
        results["directRescanWithoutPairingDialog"] = manager.requests == 1 && QApplication::topLevelWidgets().isEmpty();
        reconnect.poll(true, true, 6000); reconnect.poll(true, true, 9000); flush();
        results["connectedStopsRetries"] = manager.requests == 1;
        reconnect.poll(true, false, 11000); flush();
        reconnect.poll(true, false, 11999); flush();
        results["failedAttemptBacksOff"] = manager.requests == 2;
        reconnect.poll(true, false, 12000); flush();
        reconnect.poll(true, false, 13999); flush();
        results["retryDelayIncreases"] = manager.requests == 3;
        reconnect.poll(true, false, 14000); reconnect.poll(false, false, 14001); flush();
        results["backendStopCancelsQueuedRescan"] = manager.requests == 3;
    }
    {
        LocalDockReconnect reconnect(&app, [&] { return &manager; });
        QDialog userDialog;
        userDialog.show();
        const int before = manager.requests;
        reconnect.poll(true, false, 20000); flush(); reconnect.poll(false, false, 20100);
        results["userDialogUntouched"] = manager.requests == before + 1 && userDialog.isVisible();
    }
    {
        QObject unknown;
        LocalDockReconnect reconnect(&app, [&] { return &unknown; });
        const int before = manager.requests;
        reconnect.poll(true, false, 30000); flush();
        results["unknownManagerDoesNotRun"] = manager.requests == before && !reconnect.status()["supported"].toBool();
    }
    {
        LocalDockReconnect reconnect(&app);
        reconnect.poll(true, false, 40000); flush();
        results["differentHostImageDoesNotRun"] = !reconnect.status()["supported"].toBool() && reconnect.status()["attempts"].toInt() == 0;
    }
    QTextStream(stdout) << QJsonDocument(results).toJson(QJsonDocument::Compact) << '\n';
    for (const auto &value : results) if (!value.toBool()) return 1;
    return 0;
}

int main(int argc, char **argv) {
    QApplication app(argc, argv);
    if (app.arguments().contains("--reconnect-contract")) return reconnectContract(app);
    QString token(40, 'x');
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return 2;
    const QString endpoint = "http://127.0.0.1:" + QString::number(server.serverPort());
    QJsonArray requests;
    QJsonObject state{{"serial", "HGMOCK000003"}, {"available", true}, {"connected", true},
                      {"knobBrightness", 64}, {"followDisplayBrightness", false}};
    QObject::connect(&server, &QTcpServer::newConnection, &app, [&] {
        auto *socket = server.nextPendingConnection();
        auto buffer = std::make_shared<QByteArray>();
        QObject::connect(socket, &QTcpSocket::readyRead, &app, [&, socket, buffer] {
            buffer->append(socket->readAll());
            const int split = buffer->indexOf("\r\n\r\n");
            if (split < 0) return;
            const auto headers = buffer->left(split);
            int length = 0;
            for (const auto &line : headers.split('\n'))
                if (line.toLower().startsWith("content-length:")) length = line.mid(15).trimmed().toInt();
            if (buffer->size() < split + 4 + length) return;
            const bool authenticated = headers.contains("Authorization: Bearer " + token.toUtf8());
            QJsonObject result;
            if (headers.startsWith("GET /state")) result = state;
            else {
                auto payload = QJsonDocument::fromJson(buffer->mid(split + 4, length)).object();
                payload["authenticated"] = authenticated;
                requests.append(payload);
                if (payload["action"].toString() == "knobBrightness") state["knobBrightness"] = payload["value"];
                result = QJsonObject{{"ok", authenticated}};
            }
            const auto body = QJsonDocument(result).toJson(QJsonDocument::Compact);
            socket->write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: " +
                          QByteArray::number(body.size()) + "\r\n\r\n" + body);
            socket->disconnectFromHost();
        });
        QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
    });
    QWidget window;
    window.setObjectName("MainWindow");
    auto *layout = new QVBoxLayout(&window);
    auto *serialRow = new QWidget(&window);
    serialRow->setObjectName("serialNumberRow");
    auto *serialLayout = new QHBoxLayout(serialRow);
    auto *serial = new QPushButton("REAL-ELGATO-123", serialRow);
    serial->setObjectName("lb_SNValue");
    serialLayout->addWidget(serial);
    auto *brightness = new QWidget(&window);
    brightness->setObjectName("brightnessRow");
    auto *brightnessLayout = new QHBoxLayout(brightness);
    auto *nativeLabel = new QLabel("Brightness:", brightness);
    nativeLabel->setObjectName("lb_text1");
    auto *nativeSlider = new QSlider(Qt::Horizontal, brightness);
    nativeSlider->setObjectName("brightnessSliderbar");
    nativeSlider->setRange(0, 100);
    auto *nativeDarker = new QPushButton(brightness);
    nativeDarker->setObjectName("iconDarker");
    auto *nativeBrighter = new QPushButton(brightness);
    nativeBrighter->setObjectName("iconBrighter");
    brightnessLayout->addWidget(nativeLabel);
    brightnessLayout->addWidget(nativeDarker);
    brightnessLayout->addWidget(nativeSlider, 1);
    brightnessLayout->addWidget(nativeBrighter);
    auto *firmwareRow = new QWidget(&window);
    auto *firmwareLayout = new QHBoxLayout(firmwareRow);
    auto *firmware = new QPushButton("Update firmware", firmwareRow);
    firmware->setObjectName("clb_updateFW");
    firmware->setMinimumSize(80, 24);
    firmware->setMaximumSize(200, 40);
    firmwareLayout->addWidget(firmware);
    layout->addWidget(brightness);
    layout->addWidget(firmwareRow);
    layout->addWidget(serialRow);
    auto *combo = new ESDDeviceComboBox(&window);
    combo->setObjectName("cb_devices");
    auto *discovery = new QAction("Add Network Device…", combo);
    int discoveryCount = 0;
    QPointer<QDialog> discoveryDialog;
    QObject::connect(discovery, &QAction::triggered, &app, [&] {
        ++discoveryCount;
        auto *dialog = new QDialog(&window);
        dialog->setObjectName("ESDCoraPairingDialog");
        discoveryDialog = dialog;
        dialog->show();
    });
    window.show();
    auto *extension = new NativeUi(QJsonObject{{"endpoint", endpoint}, {"token", token}, {"serial", "HGMOCK000003"}});
    QJsonObject results;
    QTimer::singleShot(300, &app, [&] {
        results["realDeviceUntouched"] = window.findChild<QWidget *>("hellgatoKnobBrightnessRow") == nullptr &&
                                          firmware->minimumSize() == QSize(80, 24) && firmware->isEnabled();
        serial->setText("HGMOCK000003");
    });
    QTimer::singleShot(1500, &app, [&] {
        auto *row = window.findChild<QWidget *>("hellgatoKnobBrightnessRow");
        auto *slider = window.findChild<QSlider *>("hellgatoKnobBrightness");
        results["insertedBelowNativeBrightness"] = row && layout->indexOf(row) == layout->indexOf(brightness) + 1;
        results["stateLoaded"] = slider && slider->isEnabled() && slider->value() == 64;
        results["firmwareCollapsedOnlyForVirtual"] = firmware->maximumSize() == QSize(0, 0);
        if (slider) slider->setValue(37);
    });
    QTimer::singleShot(2300, &app, [&] {
        results["authenticatedControl"] = requests.size() == 1 && requests[0].toObject()["authenticated"].toBool() &&
                                           requests[0].toObject()["serial"].toString() == "HGMOCK000003" &&
                                           requests[0].toObject()["action"].toString() == "knobBrightness" &&
                                           requests[0].toObject()["value"].toInt() == 37;
        serial->setText("REAL-ELGATO-123");
    });
    QTimer::singleShot(2600, &app, [&] {
        auto *row = window.findChild<QWidget *>("hellgatoKnobBrightnessRow");
        results["realDeviceRestored"] = row && row->isHidden() && firmware->minimumSize() == QSize(80, 24) &&
                                        firmware->maximumSize() == QSize(200, 40) && firmware->isEnabled();
        auto *slider = window.findChild<QSlider *>("hellgatoKnobBrightness");
        if (slider) slider->setValue(99);
    });
    QTimer::singleShot(3000, &app, [&] {
        results["realDeviceCannotPost"] = requests.size() == 1;
        serial->setText("HGMOCK000004");
    });
    QTimer::singleShot(3300, &app, [&] {
        auto *row = window.findChild<QWidget *>("hellgatoKnobBrightnessRow");
        results["anotherVirtualSerialUntouched"] = row && row->isHidden() && firmware->minimumSize() == QSize(80, 24) &&
                                                   firmware->maximumSize() == QSize(200, 40);
        serial->setText("HGMOCK000003");
        token = QString(40, 'y');
        extension->configure(QJsonObject{{"endpoint", endpoint}, {"token", token}, {"serial", "HGMOCK000003"}});
    });
    QTimer::singleShot(4100, &app, [&] {
        results["reconfigurationKeepsOneRow"] = window.findChildren<QWidget *>("hellgatoKnobBrightnessRow").size() == 1;
        auto *slider = window.findChild<QSlider *>("hellgatoKnobBrightness");
        results["reconfigurationLoadsState"] = slider && slider->isEnabled() && slider->value() == 37;
        if (slider) slider->setValue(42);
    });
    QTimer::singleShot(4600, &app, [&] {
        results["reconfigurationUsesNewToken"] = requests.size() == 2 && requests[1].toObject()["authenticated"].toBool() &&
                                                 requests[1].toObject()["value"].toInt() == 42;
        auto *dialog = new QDialog(&window);
        dialog->setObjectName("ESDCoraPairingDialog");
        discoveryDialog = dialog;
        dialog->show();
        state["hostConnected"] = false;
    });
    QTimer::singleShot(5800, &app, [&] {
        results["defaultLeavesNativePairingToUser"] = discoveryCount == 0 && discoveryDialog && discoveryDialog->isVisible();
        state["hostConnected"] = true;
    });
    QTimer::singleShot(6800, &app, [&] {
        results["nativePairingDialogNotClosed"] = discoveryCount == 0 && discoveryDialog && discoveryDialog->isVisible();
        QTextStream(stdout) << QJsonDocument(results).toJson(QJsonDocument::Compact) << '\n';
        bool passed = true;
        for (const auto &value : results) if (!value.toBool()) passed = false;
        app.exit(passed ? 0 : 1);
    });
    return app.exec();
}

#include "test_host.moc"
