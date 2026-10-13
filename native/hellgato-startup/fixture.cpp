#include <windows.h>
#include <stdint.h>
#include <cstdio>
#include <cstring>
#include <vector>

extern "C" __declspec(dllexport) volatile LONG FixtureGuard = 0;
extern "C" int FixtureCtor();
extern "C" void FixtureGate();

struct Descriptor {
    uint32_t fields[14];
    uint64_t begin, end, capacity;
};
static_assert(sizeof(Descriptor) == 80, "Fixture descriptor layout changed");

Descriptor* descriptors;
bool expectOriginal;
bool footerCorrect;

extern "C" void FixtureFooter() {
    const uint32_t width = expectOriginal ? 4 : 5;
    footerCorrect = FixtureGuard == -1 && descriptors[0].fields[1] == width &&
        descriptors[0].end - descriptors[0].begin == width * 2 * 8 &&
        descriptors[0].capacity == descriptors[0].end;
    const auto* coordinates = reinterpret_cast<const int32_t*>(descriptors[0].begin);
    for (uint32_t i = 0; i < width * 2; ++i) {
        footerCorrect = footerCorrect && coordinates[i * 2] == 13 + static_cast<int32_t>(i % width) * (width == 5 ? 164 : 219)
            && coordinates[i * 2 + 1] == 12 + static_cast<int32_t>(i / width) * 160;
    }
    FixtureGuard = 1;
}

int main(int argc, char** argv) {
    bool invalidModel = false;
    for (int i = 1; i < argc; ++i) {
        expectOriginal = expectOriginal || std::strcmp(argv[i], "--expect-original") == 0;
        invalidModel = invalidModel || std::strcmp(argv[i], "--invalid-model") == 0;
        if (std::strcmp(argv[i], "--fail-after-model") == 0)
            SetEnvironmentVariableW(L"HELLGATO_STARTUP_FIXTURE_FAIL_AFTER_MODEL", L"1");
    }
    wchar_t systemFile[MAX_PATH + 32]{};
    GetSystemDirectoryW(systemFile, MAX_PATH);
    wcscat_s(systemFile, L"\\kernel32.dll");
    DWORD ignored = 0;
    DWORD versionSize = GetFileVersionInfoSizeW(systemFile, &ignored);
    std::vector<BYTE> version(versionSize);
    void* root = nullptr;
    UINT rootSize = 0;
    if (!versionSize || !GetFileVersionInfoW(systemFile, 0, versionSize, version.data()) ||
        !VerQueryValueW(version.data(), L"\\", &root, &rootSize) || !rootSize) return 10;
    HMODULE proxy = GetModuleHandleW(L"version.dll");
    using Identity = const char*(WINAPI*)();
    auto identity = reinterpret_cast<Identity>(GetProcAddress(proxy, "HellgatoStartupIdentity"));
    if (!identity || std::strcmp(identity(), "Hellgato.Startup.VERSION/1")) return 11;
    const char* exports[]{"GetFileVersionInfoA", "GetFileVersionInfoByHandle", "GetFileVersionInfoExA",
        "GetFileVersionInfoExW", "GetFileVersionInfoSizeA", "GetFileVersionInfoSizeExA", "GetFileVersionInfoSizeExW",
        "GetFileVersionInfoSizeW", "GetFileVersionInfoW", "VerFindFileA", "VerFindFileW", "VerInstallFileA",
        "VerInstallFileW", "VerLanguageNameA", "VerLanguageNameW", "VerQueryValueA", "VerQueryValueW"};
    for (WORD i = 0; i < 17; ++i) {
        const auto function = GetProcAddress(proxy, exports[i]);
        if (!function || function != GetProcAddress(proxy, MAKEINTRESOURCEA(i + 1))) return 19;
    }

    auto* node = static_cast<BYTE*>(HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, 80));
    descriptors = static_cast<Descriptor*>(HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, 160));
    auto* keys = static_cast<int32_t*>(HeapAlloc(GetProcessHeap(), 0, 64));
    auto* strip = static_cast<int32_t*>(HeapAlloc(GetProcessHeap(), 0, 8));
    if (!node || !descriptors || !keys || !strip) return 12;
    std::memcpy(node + 16, "20GBD9901", 9);
    uint64_t length = 9, capacity = 15, begin = reinterpret_cast<uint64_t>(descriptors), end = begin + 160;
    std::memcpy(node + 32, &length, 8);
    std::memcpy(node + 40, &capacity, 8);
    std::memcpy(node + 48, &begin, 8);
    std::memcpy(node + 56, &end, 8);
    std::memcpy(node + 64, &end, 8);
    const uint32_t keyFields[]{1,4,2,0,0,800,480,120,120,120,120,100,100,0};
    const uint32_t encoderFields[]{2,4,1,0,3,800,100,0xffffffff,0xffffffff,0xffffffff,0xffffffff,0xffffffff,0xffffffff,0};
    std::memcpy(descriptors[0].fields, keyFields, sizeof(keyFields));
    std::memcpy(descriptors[1].fields, encoderFields, sizeof(encoderFields));
    descriptors[0].begin = reinterpret_cast<uint64_t>(keys);
    descriptors[0].end = descriptors[0].capacity = descriptors[0].begin + 64;
    descriptors[1].begin = reinterpret_cast<uint64_t>(strip);
    descriptors[1].end = descriptors[1].capacity = descriptors[1].begin + 8;
    for (int i = 0; i < 8; ++i) { keys[i * 2] = 13 + i % 4 * 219; keys[i * 2 + 1] = 12 + i / 4 * 160; }
    strip[0] = invalidModel ? 1 : 0;
    strip[1] = 380;
    FixtureGuard = -1;
    FixtureGate();
    if (!footerCorrect || FixtureGuard != 1) return 13;
    if (FixtureCtor() != (expectOriginal ? 8 : 10)) return 14;
    if (!HeapValidate(GetProcessHeap(), 0, keys)) return 20;
    if (expectOriginal && descriptors[0].begin != reinterpret_cast<uint64_t>(keys)) return 21;

    wchar_t mappingName[96]{};
    swprintf_s(mappingName, L"Local\\Hellgato.Startup.%lu", GetCurrentProcessId());
    HANDLE mapping = OpenFileMappingW(FILE_MAP_READ, FALSE, mappingName);
    if (!mapping) return 15;
    const auto* status = static_cast<const BYTE*>(MapViewOfFile(mapping, FILE_MAP_READ, 0, 0, 56));
    if (!status) return 16;
    DWORD schema = 0, pid = 0, state = 0, error = 0;
    uint64_t created = 0, allocation = 0, descriptor = 0;
    std::memcpy(&schema, status + 8, 4);
    std::memcpy(&pid, status + 12, 4);
    std::memcpy(&created, status + 16, 8);
    std::memcpy(&state, status + 24, 4);
    std::memcpy(&error, status + 28, 4);
    std::memcpy(&allocation, status + 40, 8);
    std::memcpy(&descriptor, status + 48, 8);
    FILETIME actualCreated{}, exited{}, kernel{}, user{};
    GetProcessTimes(GetCurrentProcess(), &actualCreated, &exited, &kernel, &user);
    if (std::memcmp(status, "HGREADY1", 8) || schema != 1 || pid != GetCurrentProcessId() ||
        created != (uint64_t(actualCreated.dwLowDateTime) | uint64_t(actualCreated.dwHighDateTime) << 32)) return 17;
    if (expectOriginal ? (state != 3 && state != 4) : (state != 2 || !allocation || descriptor != reinterpret_cast<uint64_t>(descriptors))) return 18;
    std::printf("{\"state\":%lu,\"error\":%lu,\"keyCount\":%d,\"publishedAfterPatch\":true,\"versionForwarding\":true,\"exportsResolved\":17}\n",
                state, error, FixtureCtor());
    UnmapViewOfFile(status);
    CloseHandle(mapping);
    return 0;
}
