#pragma once

#include <windows.h>

#include <QApplication>
#include <QCryptographicHash>
#include <QFile>
#include <QJsonObject>
#include <QMetaMethod>
#include <QPointer>
#include <QTimer>

#include <algorithm>
#include <cstring>
#include <functional>

// This private singleton layout is verified only for the exact host image below.
// Other host builds keep their ordinary pairing behavior.
class LocalDockReconnect : public QObject {
public:
    using Resolver = std::function<QObject *()>;
    explicit LocalDockReconnect(QObject *parent, Resolver resolver = {}) : QObject(parent), resolver_(std::move(resolver)) {}

    void poll(bool eligible, bool connected, qint64 now) {
        eligible_ = eligible && !connected;
        if (!eligible_) {
            ++epoch_;
            pending_ = false;
            retryDelay_ = 1000;
            nextAttempt_ = now + 1000;
            return;
        }
        if (pending_ || now < nextAttempt_) return;
        auto *manager = resolver_ ? resolver_() : nativeManager();
        supported_ = validManager(manager);
        if (!supported_) { nextAttempt_ = now + 30000; return; }
        pending_ = true;
        const auto epoch = epoch_;
        nextAttempt_ = now + retryDelay_;
        retryDelay_ = std::min<qint64>(30000, retryDelay_ * 2);
        QTimer::singleShot(0, this, [this, epoch, manager = QPointer<QObject>(manager)] {
            if (epoch != epoch_ || !eligible_) return;
            pending_ = false;
            if (validManager(manager) && QMetaObject::invokeMethod(manager, "onRescan", Qt::DirectConnection)) ++attempts_;
        });
    }

    QJsonObject status() const {
        return {{"attempts", attempts_}, {"pending", pending_}, {"supported", supported_}};
    }

private:
    static bool validManager(QObject *manager) {
        if (!manager || qstrcmp(manager->metaObject()->className(), "ESDCoraDocksManagerEx") != 0 ||
            manager->thread() != qApp->thread()) return false;
        const auto index = manager->metaObject()->indexOfMethod("onRescan()");
        if (index < 0) return false;
        const auto method = manager->metaObject()->method(index);
        return method.methodType() == QMetaMethod::Slot && method.parameterCount() == 0 && method.returnType() == QMetaType::Void;
    }

    QObject *nativeManager() {
        if (!hostChecked_) {
            hostChecked_ = true;
            QFile host(QCoreApplication::applicationFilePath());
            hostSupported_ = QByteArray(qVersion()) == "6.11.1" && host.open(QIODevice::ReadOnly) &&
                QCryptographicHash::hash(host.readAll(), QCryptographicHash::Sha256).toHex() ==
                    "2963df27b13f97e5736e3f931348d89d2f693109f3cb9dc3c9899300e35ea9e3";
        }
        if (!hostSupported_) return nullptr;
        const auto base = reinterpret_cast<quintptr>(GetModuleHandleW(nullptr));
        auto *manager = reinterpret_cast<QObject *>(base + 0x1a1a1e0);
        MEMORY_BASIC_INFORMATION region{};
        if (!VirtualQuery(manager, &region, sizeof(region)) || region.State != MEM_COMMIT ||
            region.Protect & (PAGE_GUARD | PAGE_NOACCESS) ||
            reinterpret_cast<quintptr>(manager) + sizeof(quintptr) > reinterpret_cast<quintptr>(region.BaseAddress) + region.RegionSize)
            return nullptr;
        quintptr vtable = 0;
        std::memcpy(&vtable, manager, sizeof(vtable));
        if (vtable != base + 0x169d5e8) return nullptr;
        return manager;
    }

    Resolver resolver_;
    bool eligible_ = false, pending_ = false, supported_ = false, hostChecked_ = false, hostSupported_ = false;
    int attempts_ = 0;
    quint64 epoch_ = 0;
    qint64 nextAttempt_ = 0, retryDelay_ = 1000;
};
