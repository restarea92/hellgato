#include <windows.h>
#include <bcrypt.h>
#include <stdint.h>
#include <algorithm>
#include <array>
#include <cstring>
#include <cwchar>
#include <new>
#include <vector>

namespace {
constexpr DWORD kSchema = 1;
constexpr size_t kMaximumPlan = 2 * 1024 * 1024;
constexpr size_t kMaximumGuards = 64;
constexpr size_t kMaximumGuardBytes = 1024 * 1024;
constexpr size_t kAllocationSize = 0x3000;
constexpr DWORD kPreparing = 1, kReady = 2, kIncompatible = 3, kError = 4;
enum Error : DWORD { None, PlanMissing, PlanInvalid, HashMismatch, CodeMismatch,
    AllocationFailed, ModelMismatch, WriteFailed, UnexpectedState };

#pragma pack(push, 1)
struct Status {
    char magic[8];
    DWORD schema;
    DWORD pid;
    uint64_t created;
    volatile LONG state;
    DWORD error;
    uint64_t imageBase;
    uint64_t feedbackAllocation;
    uint64_t descriptor;
};
#pragma pack(pop)
static_assert(sizeof(Status) == 56, "Startup status contract changed");

struct Failure { Error error; };
struct Relocation { DWORD offset; DWORD kind; uint64_t addend; };
struct Guard { DWORD rva; std::vector<BYTE> expected; };
struct Patch {
    DWORD rva;
    std::vector<BYTE> expected;
    std::vector<BYTE> desired;
    std::vector<Relocation> relocations;
};
struct Plan {
    DWORD imageSize, timestamp, entryRva, constructorRva, gateRva, modelGuardRva;
    std::array<BYTE, 32> hash;
    std::vector<Guard> guards;
    std::vector<BYTE> feedback;
    std::vector<Relocation> relocations;
    std::vector<Patch> patches;
};
struct Runtime {
    Plan plan;
    BYTE* feedback = nullptr;
    size_t applied = 0;
    bool gateArmed = false;
    BYTE gateOriginal = 0;
    BYTE* descriptor = nullptr;
    std::array<BYTE, 80> originalDescriptor{};
    BYTE* replacement = nullptr;
    bool modelWritten = false;
};

HMODULE gModule;
BYTE* gImage;
DWORD gImageSize;
BYTE* gEntry;
BYTE gEntryOriginal;
PVOID gHandler;
Status* gStatus;
Runtime* gRuntime;
volatile LONG gEntryHandled;
volatile LONG gGateHandled;
INIT_ONCE gVersionOnce = INIT_ONCE_STATIC_INIT;
HMODULE gSystemVersion;

void require(bool condition, Error error) {
    if (!condition) throw Failure{error};
}

bool readable(const void* source, size_t length) {
    uintptr_t cursor = reinterpret_cast<uintptr_t>(source);
    const uintptr_t end = cursor + length;
    if (end < cursor || !cursor) return false;
    while (cursor < end) {
        MEMORY_BASIC_INFORMATION region{};
        if (!VirtualQuery(reinterpret_cast<void*>(cursor), &region, sizeof(region))) return false;
        const DWORD access = region.Protect & 0xff;
        if (region.State != MEM_COMMIT || (region.Protect & PAGE_GUARD) ||
            (access != PAGE_READONLY && access != PAGE_READWRITE && access != PAGE_WRITECOPY &&
             access != PAGE_EXECUTE_READ && access != PAGE_EXECUTE_READWRITE &&
             access != PAGE_EXECUTE_WRITECOPY)) return false;
        const uintptr_t next = reinterpret_cast<uintptr_t>(region.BaseAddress) + region.RegionSize;
        if (next <= cursor) return false;
        cursor = next;
    }
    return true;
}

bool copy_memory(const void* source, void* destination, size_t length) {
    SIZE_T copied = 0;
    return readable(source, length) && ReadProcessMemory(GetCurrentProcess(), source, destination,
                                                       length, &copied) && copied == length;
}

bool equal_memory(const void* source, const void* expected, size_t length) {
    if (!readable(source, length)) return false;
    __try { return std::memcmp(source, expected, length) == 0; }
    __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}

bool write_code(BYTE* destination, const BYTE* bytes, size_t length) {
    DWORD old = 0;
    if (!VirtualProtect(destination, length, PAGE_EXECUTE_READWRITE, &old)) return false;
    SIZE_T copied = 0;
    const bool written = WriteProcessMemory(GetCurrentProcess(), destination, bytes, length, &copied)
        && copied == length;
    const bool flushed = FlushInstructionCache(GetCurrentProcess(), destination, length) != FALSE;
    DWORD ignored = 0;
    const bool restored = VirtualProtect(destination, length, old, &ignored) != FALSE;
    return written && flushed && restored && equal_memory(destination, bytes, length);
}

void publish(DWORD state, Error error = None) {
    if (!gStatus) return;
    gStatus->error = error;
    MemoryBarrier();
    InterlockedExchange(&gStatus->state, state);
}

void create_status() {
    wchar_t name[96]{};
    swprintf_s(name, L"Local\\Hellgato.Startup.%lu", GetCurrentProcessId());
    HANDLE mapping = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(Status), name);
    require(mapping != nullptr, AllocationFailed);
    gStatus = static_cast<Status*>(MapViewOfFile(mapping, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(Status)));
    if (!gStatus) { CloseHandle(mapping); throw Failure{AllocationFailed}; }
    // The handle and view intentionally retain process-lifetime readiness evidence.
    std::memset(gStatus, 0, sizeof(Status));
    std::memcpy(gStatus->magic, "HGREADY1", 8);
    gStatus->schema = kSchema;
    gStatus->pid = GetCurrentProcessId();
    FILETIME created{}, exited{}, kernel{}, user{};
    require(GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user) != FALSE, UnexpectedState);
    gStatus->created = uint64_t(created.dwLowDateTime) | (uint64_t(created.dwHighDateTime) << 32);
    gStatus->imageBase = reinterpret_cast<uint64_t>(gImage);
    publish(kPreparing);
}

BOOL CALLBACK load_system_version(PINIT_ONCE, PVOID, PVOID*) {
    wchar_t path[MAX_PATH + 32]{};
    const UINT length = GetSystemDirectoryW(path, MAX_PATH);
    if (!length || length >= MAX_PATH) return FALSE;
    std::memcpy(path + length, L"\\version.dll", sizeof(L"\\version.dll"));
    gSystemVersion = LoadLibraryExW(path, nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    return gSystemVersion != nullptr;
}

FARPROC version_export(const char* name) {
    if (!InitOnceExecuteOnce(&gVersionOnce, load_system_version, nullptr, nullptr)) return nullptr;
    return GetProcAddress(gSystemVersion, name);
}

struct Reader {
    const std::vector<BYTE>& data;
    size_t position = 0;
    template<class T> T scalar() {
        require(position <= data.size() && sizeof(T) <= data.size() - position, PlanInvalid);
        T value;
        std::memcpy(&value, data.data() + position, sizeof(value));
        position += sizeof(value);
        return value;
    }
    std::vector<BYTE> bytes(size_t length) {
        require(position <= data.size() && length <= data.size() - position, PlanInvalid);
        std::vector<BYTE> result(data.begin() + position, data.begin() + position + length);
        position += length;
        return result;
    }
};

bool image_range(DWORD rva, size_t size) {
    return rva < gImageSize && size && size <= gImageSize - rva;
}

std::vector<Relocation> read_relocations(Reader& reader, size_t size) {
    DWORD count = reader.scalar<DWORD>();
    require(count <= 256, PlanInvalid);
    std::vector<Relocation> result;
    for (DWORD i = 0; i < count; ++i) {
        Relocation relocation{reader.scalar<DWORD>(), reader.scalar<DWORD>(), reader.scalar<uint64_t>()};
        require(relocation.offset <= size && 8 <= size - relocation.offset &&
                (relocation.kind == 1 || relocation.kind == 2), PlanInvalid);
        require(relocation.addend < (relocation.kind == 1 ? gImageSize : kAllocationSize), PlanInvalid);
        for (const auto& previous : result)
            require(relocation.offset + 8 <= previous.offset || previous.offset + 8 <= relocation.offset, PlanInvalid);
        result.push_back(relocation);
    }
    return result;
}

std::vector<BYTE> read_plan_file() {
    wchar_t path[32768]{};
    const DWORD length = GetModuleFileNameW(gModule, path, static_cast<DWORD>(std::size(path)));
    require(length > 0 && length < std::size(path), PlanMissing);
    wchar_t* separator = wcsrchr(path, L'\\');
    require(separator && (separator - path) + 23 < std::size(path), PlanMissing);
    wcscpy_s(separator + 1, std::size(path) - (separator + 1 - path), L"HellgatoStartup.plan");
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
    require(file != INVALID_HANDLE_VALUE, PlanMissing);
    LARGE_INTEGER size{};
    if (!GetFileSizeEx(file, &size) || size.QuadPart < 72 || size.QuadPart > kMaximumPlan) {
        CloseHandle(file); throw Failure{PlanInvalid};
    }
    std::vector<BYTE> bytes(static_cast<size_t>(size.QuadPart));
    DWORD read = 0;
    const bool ok = ReadFile(file, bytes.data(), static_cast<DWORD>(bytes.size()), &read, nullptr)
        && read == bytes.size();
    CloseHandle(file);
    require(ok, PlanInvalid);
    return bytes;
}

Plan parse_plan(const std::vector<BYTE>& bytes) {
    Reader reader{bytes};
    require(reader.bytes(8) == std::vector<BYTE>({'H','G','S','T','A','R','T','1'}), PlanInvalid);
    require(reader.scalar<DWORD>() == kSchema, PlanInvalid);
    Plan plan{};
    plan.imageSize = reader.scalar<DWORD>();
    plan.timestamp = reader.scalar<DWORD>();
    plan.entryRva = reader.scalar<DWORD>();
    plan.constructorRva = reader.scalar<DWORD>();
    plan.gateRva = reader.scalar<DWORD>();
    plan.modelGuardRva = reader.scalar<DWORD>();
    const auto hash = reader.bytes(32);
    std::copy(hash.begin(), hash.end(), plan.hash.begin());
    require(plan.imageSize == gImageSize && image_range(plan.entryRva, 1) &&
            image_range(plan.constructorRva, 10) && image_range(plan.gateRva, 5) &&
            image_range(plan.modelGuardRva, 4) && gEntry == gImage + plan.entryRva, PlanInvalid);
    DWORD count = reader.scalar<DWORD>();
    require(count > 0 && count <= kMaximumGuards, PlanInvalid);
    size_t total = 0;
    for (DWORD i = 0; i < count; ++i) {
        DWORD rva = reader.scalar<DWORD>(), length = reader.scalar<DWORD>();
        require(image_range(rva, length) && length <= kMaximumGuardBytes &&
                total + length <= kMaximumGuardBytes, PlanInvalid);
        total += length;
        plan.guards.push_back({rva, reader.bytes(length)});
    }
    DWORD length = reader.scalar<DWORD>();
    require(length > 0 && length <= 4096, PlanInvalid);
    plan.feedback = reader.bytes(length);
    plan.relocations = read_relocations(reader, length);
    count = reader.scalar<DWORD>();
    require(count > 0 && count <= 32, PlanInvalid);
    bool constructorFound = false;
    for (DWORD i = 0; i < count; ++i) {
        DWORD rva = reader.scalar<DWORD>();
        length = reader.scalar<DWORD>();
        require(image_range(rva, length) && length <= 256, PlanInvalid);
        for (const auto& previous : plan.patches)
            require(uint64_t(rva) + length <= previous.rva ||
                    uint64_t(previous.rva) + previous.expected.size() <= rva, PlanInvalid);
        require(!(rva <= plan.gateRva && plan.gateRva < uint64_t(rva) + length) &&
                !(rva <= plan.entryRva && plan.entryRva < uint64_t(rva) + length), PlanInvalid);
        Patch patch{rva, reader.bytes(length), reader.bytes(length), {}};
        patch.relocations = read_relocations(reader, length);
        if (rva == plan.constructorRva) {
            static constexpr BYTE before[]{0xba,4,0,0,0,0x41,0xb8,2,0,0,0};
            static constexpr BYTE after[]{0xba,5,0,0,0,0x41,0xb8,2,0,0,0};
            require(patch.expected == std::vector<BYTE>(std::begin(before), std::end(before)) &&
                    patch.desired == std::vector<BYTE>(std::begin(after), std::end(after)) &&
                    patch.relocations.empty(), PlanInvalid);
            constructorFound = true;
        }
        plan.patches.push_back(std::move(patch));
    }
    require(constructorFound && reader.position == bytes.size(), PlanInvalid);
    return plan;
}

std::array<BYTE, 32> host_hash() {
    wchar_t path[32768]{};
    DWORD length = GetModuleFileNameW(nullptr, path, static_cast<DWORD>(std::size(path)));
    require(length > 0 && length < std::size(path), HashMismatch);
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    require(file != INVALID_HANDLE_VALUE, HashMismatch);
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    std::array<BYTE, 32> output{};
    bool ok = BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) >= 0;
    DWORD objectSize = 0, returned = 0;
    if (ok) ok = BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
                                  reinterpret_cast<BYTE*>(&objectSize), sizeof(objectSize), &returned, 0) >= 0;
    std::vector<BYTE> object;
    if (ok && objectSize <= 1024 * 1024) object.resize(objectSize); else ok = false;
    if (ok) ok = BCryptCreateHash(algorithm, &hash, object.data(), objectSize, nullptr, 0, 0) >= 0;
    std::array<BYTE, 65536> buffer{};
    while (ok) {
        DWORD read = 0;
        if (!ReadFile(file, buffer.data(), static_cast<DWORD>(buffer.size()), &read, nullptr)) { ok = false; break; }
        if (!read) break;
        ok = BCryptHashData(hash, buffer.data(), read, 0) >= 0;
    }
    if (ok) ok = BCryptFinishHash(hash, output.data(), static_cast<DWORD>(output.size()), 0) >= 0;
    if (hash) BCryptDestroyHash(hash);
    if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
    CloseHandle(file);
    require(ok, HashMismatch);
    return output;
}

void relocate(std::vector<BYTE>& bytes, const std::vector<Relocation>& relocations, BYTE* allocation) {
    for (const auto& relocation : relocations) {
        const uint64_t address = reinterpret_cast<uint64_t>(relocation.kind == 1 ? gImage : allocation) + relocation.addend;
        std::memcpy(bytes.data() + relocation.offset, &address, 8);
    }
}

bool rollback(Runtime* runtime) {
    if (!runtime) return true;
    bool ok = true;
    if (runtime->modelWritten) {
        SIZE_T written = 0;
        ok = WriteProcessMemory(GetCurrentProcess(), runtime->descriptor, runtime->originalDescriptor.data(),
                                runtime->originalDescriptor.size(), &written) &&
             written == runtime->originalDescriptor.size() &&
             equal_memory(runtime->descriptor, runtime->originalDescriptor.data(), runtime->originalDescriptor.size());
        runtime->modelWritten = false;
    }
    if (runtime->gateArmed) {
        ok = write_code(gImage + runtime->plan.gateRva, &runtime->gateOriginal, 1) && ok;
        runtime->gateArmed = false;
    }
    while (runtime->applied) {
        const auto& patch = runtime->plan.patches[--runtime->applied];
        ok = write_code(gImage + patch.rva, patch.expected.data(), patch.expected.size()) && ok;
    }
    // Trampolines and staged vectors remain valid until process exit even after rollback.
    return ok;
}

void prepare_code() {
    gRuntime = new Runtime;
    auto& runtime = *gRuntime;
    runtime.plan = parse_plan(read_plan_file());
    auto& plan = runtime.plan;
    const auto* dos = reinterpret_cast<IMAGE_DOS_HEADER*>(gImage);
    const auto* nt = reinterpret_cast<IMAGE_NT_HEADERS64*>(gImage + dos->e_lfanew);
    require(nt->FileHeader.TimeDateStamp == plan.timestamp && nt->OptionalHeader.SizeOfImage == plan.imageSize,
            CodeMismatch);
    require(host_hash() == plan.hash, HashMismatch);
    LONG initialized = 0;
    require(copy_memory(gImage + plan.modelGuardRva, &initialized, sizeof(initialized)) && initialized == 0,
            UnexpectedState);
    for (const auto& guard : plan.guards)
        require(equal_memory(gImage + guard.rva, guard.expected.data(), guard.expected.size()), CodeMismatch);
    for (const auto& patch : plan.patches)
        require(equal_memory(gImage + patch.rva, patch.expected.data(), patch.expected.size()), CodeMismatch);
    require(*(gImage + plan.gateRva) == 0xe8, CodeMismatch);
    runtime.gateOriginal = 0xe8;
    runtime.feedback = static_cast<BYTE*>(VirtualAlloc(nullptr, kAllocationSize, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
    require(runtime.feedback != nullptr, AllocationFailed);
    relocate(plan.feedback, plan.relocations, runtime.feedback);
    std::memcpy(runtime.feedback, plan.feedback.data(), plan.feedback.size());
    DWORD old = 0;
    require(VirtualProtect(runtime.feedback, 4096, PAGE_EXECUTE_READ, &old) &&
            FlushInstructionCache(GetCurrentProcess(), runtime.feedback, 4096), WriteFailed);
    for (auto& patch : plan.patches) relocate(patch.desired, patch.relocations, runtime.feedback);
    for (const auto& patch : plan.patches) {
        ++runtime.applied;
        require(write_code(gImage + patch.rva, patch.desired.data(), patch.desired.size()), WriteFailed);
    }
    const BYTE breakpoint = 0xcc;
    runtime.gateArmed = true;
    require(write_code(gImage + plan.gateRva, &breakpoint, 1), WriteFailed);
    gStatus->feedbackAllocation = reinterpret_cast<uint64_t>(runtime.feedback);
}

struct Candidate {
    BYTE* descriptor = nullptr;
    std::array<BYTE, 80> raw{};
    BYTE* coordinates = nullptr;
};

template<class T> T at(const BYTE* data, size_t offset) {
    T value;
    std::memcpy(&value, data + offset, sizeof(value));
    return value;
}

bool candidate_at(BYTE* node, Candidate& candidate) {
    std::array<BYTE, 80> model{};
    if (!copy_memory(node, model.data(), model.size())) return false;
    const auto begin = at<uint64_t>(model.data(), 48);
    if (!begin || begin % 8 || at<uint64_t>(model.data(), 56) != begin + 160 ||
        at<uint64_t>(model.data(), 64) != begin + 160) return false;
    std::array<BYTE, 160> descriptors{};
    if (!copy_memory(reinterpret_cast<void*>(begin), descriptors.data(), descriptors.size())) return false;
    constexpr uint32_t keys[]{1,4,2,0,0,800,480,120,120,120,120,100,100};
    constexpr uint32_t encoders[]{2,4,1,0,3,800,100,0xffffffff,0xffffffff,0xffffffff,0xffffffff,0xffffffff,0xffffffff};
    if (std::memcmp(descriptors.data(), keys, sizeof(keys)) ||
        std::memcmp(descriptors.data() + 80, encoders, sizeof(encoders))) return false;
    const auto coordinates = at<uint64_t>(descriptors.data(), 56);
    if (!coordinates || coordinates % 8 || at<uint64_t>(descriptors.data(), 64) != coordinates + 64 ||
        at<uint64_t>(descriptors.data(), 72) != coordinates + 64) return false;
    int32_t points[16]{};
    if (!copy_memory(reinterpret_cast<void*>(coordinates), points, sizeof(points))) return false;
    for (size_t i = 0; i < 8; ++i)
        if (points[i * 2] != 13 + static_cast<int32_t>(i % 4) * 219 ||
            points[i * 2 + 1] != 12 + static_cast<int32_t>(i / 4) * 160) return false;
    const auto encoder = at<uint64_t>(descriptors.data(), 80 + 56);
    if (!encoder || encoder % 8 || at<uint64_t>(descriptors.data(), 80 + 64) != encoder + 8 ||
        at<uint64_t>(descriptors.data(), 80 + 72) != encoder + 8) return false;
    int32_t strip[2]{};
    if (!copy_memory(reinterpret_cast<void*>(encoder), strip, sizeof(strip)) || strip[0] != 0 || strip[1] != 380) return false;
    candidate.descriptor = reinterpret_cast<BYTE*>(begin);
    std::copy_n(descriptors.data(), candidate.raw.size(), candidate.raw.begin());
    candidate.coordinates = reinterpret_cast<BYTE*>(coordinates);
    return true;
}

Candidate find_model() {
    BYTE marker[32]{};
    std::memcpy(marker, "20GBD9901", 9);
    marker[16] = 9;
    marker[24] = 15;
    std::vector<BYTE> buffer(65536);
    Candidate found;
    SYSTEM_INFO system{};
    GetSystemInfo(&system);
    uintptr_t cursor = reinterpret_cast<uintptr_t>(system.lpMinimumApplicationAddress);
    const uintptr_t maximum = reinterpret_cast<uintptr_t>(system.lpMaximumApplicationAddress);
    const ULONGLONG deadline = GetTickCount64() + 5000;
    size_t scanned = 0, regions = 0;
    while (cursor < maximum) {
        MEMORY_BASIC_INFORMATION region{};
        require(VirtualQuery(reinterpret_cast<void*>(cursor), &region, sizeof(region)) != 0, ModelMismatch);
        const uintptr_t end = reinterpret_cast<uintptr_t>(region.BaseAddress) + region.RegionSize;
        require(end > cursor && ++regions < 65536 && GetTickCount64() <= deadline, ModelMismatch);
        if (region.State == MEM_COMMIT && region.Type == MEM_PRIVATE &&
            (region.Protect == PAGE_READWRITE || region.Protect == PAGE_WRITECOPY)) {
            uintptr_t start = reinterpret_cast<uintptr_t>(region.BaseAddress);
            while (start < end) {
                const size_t count = (std::min)(buffer.size(), static_cast<size_t>(end - start));
                scanned += count;
                require(scanned <= size_t(1024) * 1024 * 1024 && GetTickCount64() <= deadline, ModelMismatch);
                SIZE_T copied = 0;
                if (ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<void*>(start), buffer.data(), count, &copied)
                    && copied == count) {
                    for (size_t offset = 16; offset + sizeof(marker) <= count; ++offset) {
                        if ((start + offset) % 8 || buffer[offset] != marker[0] ||
                            std::memcmp(buffer.data() + offset, marker, sizeof(marker))) continue;
                        Candidate candidate;
                        if (!candidate_at(reinterpret_cast<BYTE*>(start + offset - 16), candidate)) continue;
                        require(!found.descriptor || candidate.descriptor == found.descriptor, ModelMismatch);
                        found = candidate;
                    }
                }
                if (count <= 256) break;
                start += count - 256;
            }
        }
        cursor = end;
    }
    require(found.descriptor != nullptr, ModelMismatch);
    return found;
}

void prepare_model() {
    auto& runtime = *gRuntime;
    LONG guard = 0;
    require(copy_memory(gImage + runtime.plan.modelGuardRva, &guard, sizeof(guard)) && guard == -1,
            UnexpectedState);
    Candidate candidate = find_model();
    require(HeapValidate(GetProcessHeap(), 0, candidate.coordinates) != FALSE, ModelMismatch);
    runtime.replacement = static_cast<BYTE*>(HeapAlloc(GetProcessHeap(), 0, 80));
    require(runtime.replacement != nullptr, AllocationFailed);
    int32_t points[20]{};
    for (size_t i = 0; i < 10; ++i) {
        points[i * 2] = 13 + static_cast<int32_t>(i % 5) * 164;
        points[i * 2 + 1] = 12 + static_cast<int32_t>(i / 5) * 160;
    }
    std::memcpy(runtime.replacement, points, sizeof(points));
    runtime.descriptor = candidate.descriptor;
    runtime.originalDescriptor = candidate.raw;
    require(equal_memory(candidate.descriptor, candidate.raw.data(), candidate.raw.size()), ModelMismatch);
    auto patched = candidate.raw;
    const uint32_t width = 5;
    const uint64_t begin = reinterpret_cast<uint64_t>(runtime.replacement), end = begin + 80;
    std::memcpy(patched.data() + 4, &width, 4);
    std::memcpy(patched.data() + 56, &begin, 8);
    std::memcpy(patched.data() + 64, &end, 8);
    std::memcpy(patched.data() + 72, &end, 8);
    runtime.modelWritten = true;
    SIZE_T written = 0;
    require(WriteProcessMemory(GetCurrentProcess(), candidate.descriptor, patched.data(), patched.size(), &written)
            && written == patched.size() && equal_memory(candidate.descriptor, patched.data(), patched.size())
            && equal_memory(runtime.replacement, points, sizeof(points)), WriteFailed);
#ifdef HELLGATO_STARTUP_TEST_HOST
    wchar_t injectedFailure[2]{};
    require(!GetEnvironmentVariableW(L"HELLGATO_STARTUP_FIXTURE_FAIL_AFTER_MODEL", injectedFailure,
                                     static_cast<DWORD>(std::size(injectedFailure))), WriteFailed);
#endif
    require(write_code(gImage + runtime.plan.gateRva, &runtime.gateOriginal, 1), WriteFailed);
    runtime.gateArmed = false;
    gStatus->descriptor = reinterpret_cast<uint64_t>(candidate.descriptor);
    publish(kReady);
}

LONG CALLBACK exception_handler(EXCEPTION_POINTERS* exception) {
    if (!exception || exception->ExceptionRecord->ExceptionCode != EXCEPTION_BREAKPOINT)
        return EXCEPTION_CONTINUE_SEARCH;
    BYTE* address = static_cast<BYTE*>(exception->ExceptionRecord->ExceptionAddress);
    if (address == gEntry && InterlockedCompareExchange(&gEntryHandled, 1, 0) == 0) {
        if (!write_code(gEntry, &gEntryOriginal, 1)) return EXCEPTION_CONTINUE_SEARCH;
        exception->ContextRecord->Rip = reinterpret_cast<DWORD64>(gEntry);
        try {
            create_status();
            // Version installation exports are resolved after the loader releases its lock.
            InitOnceExecuteOnce(&gVersionOnce, load_system_version, nullptr, nullptr);
            prepare_code();
        } catch (const Failure& failure) {
            const bool restored = rollback(gRuntime);
            publish(restored ? kIncompatible : kError, restored ? failure.error : WriteFailed);
        } catch (...) {
            rollback(gRuntime);
            publish(kError, AllocationFailed);
        }
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    if (gRuntime && gRuntime->gateArmed && address == gImage + gRuntime->plan.gateRva &&
        InterlockedCompareExchange(&gGateHandled, 1, 0) == 0) {
        exception->ContextRecord->Rip = reinterpret_cast<DWORD64>(address);
        try {
            prepare_model();
        } catch (const Failure& failure) {
            const bool restored = rollback(gRuntime);
            publish(restored ? kIncompatible : kError, restored ? failure.error : WriteFailed);
        } catch (...) {
            rollback(gRuntime);
            publish(kError, AllocationFailed);
        }
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

bool supported_host() {
    wchar_t path[32768]{};
    DWORD length = GetModuleFileNameW(nullptr, path, static_cast<DWORD>(std::size(path)));
    if (!length || length >= std::size(path)) return false;
    const wchar_t* name = wcsrchr(path, L'\\');
    name = name ? name + 1 : path;
    if (_wcsicmp(name, L"StreamDeck.exe") == 0) return true;
#ifdef HELLGATO_STARTUP_TEST_HOST
    if (_wcsicmp(name, L"HellgatoStartupFixture.exe") == 0) return true;
#endif
    return false;
}

bool arm_entry() {
    __try {
        gImage = reinterpret_cast<BYTE*>(GetModuleHandleW(nullptr));
        const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(gImage);
        if (!gImage || dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew < 64 || dos->e_lfanew > 1024 * 1024) return false;
        const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(gImage + dos->e_lfanew);
        if (nt->Signature != IMAGE_NT_SIGNATURE || nt->FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 ||
            nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC ||
            nt->OptionalHeader.SizeOfImage > 256 * 1024 * 1024 ||
            nt->OptionalHeader.AddressOfEntryPoint >= nt->OptionalHeader.SizeOfImage) return false;
        gImageSize = nt->OptionalHeader.SizeOfImage;
        gEntry = gImage + nt->OptionalHeader.AddressOfEntryPoint;
        gEntryOriginal = *gEntry;
        if (gEntryOriginal == 0xcc) return false;
        gHandler = AddVectoredExceptionHandler(1, exception_handler);
        if (!gHandler) return false;
        const BYTE breakpoint = 0xcc;
        if (!write_code(gEntry, &breakpoint, 1)) {
            write_code(gEntry, &gEntryOriginal, 1);
            RemoveVectoredExceptionHandler(gHandler);
            gHandler = nullptr;
            return false;
        }
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
}

extern "C" const char* WINAPI HellgatoStartupIdentity() {
    return "Hellgato.Startup.VERSION/1";
}

extern "C" DWORD WINAPI ForwardVerInstallFileA(DWORD flags, LPCSTR source, LPCSTR destination,
        LPCSTR sourceDirectory, LPCSTR destinationDirectory, LPCSTR currentDirectory,
        LPSTR temporary, PUINT length) {
    using Function = DWORD(WINAPI*)(DWORD, LPCSTR, LPCSTR, LPCSTR, LPCSTR, LPCSTR, LPSTR, PUINT);
    auto function = reinterpret_cast<Function>(version_export("VerInstallFileA"));
    return function ? function(flags, source, destination, sourceDirectory, destinationDirectory,
                               currentDirectory, temporary, length) : VIF_CANNOTREADSRC;
}

extern "C" DWORD WINAPI ForwardVerInstallFileW(DWORD flags, LPCWSTR source, LPCWSTR destination,
        LPCWSTR sourceDirectory, LPCWSTR destinationDirectory, LPCWSTR currentDirectory,
        LPWSTR temporary, PUINT length) {
    using Function = DWORD(WINAPI*)(DWORD, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, LPWSTR, PUINT);
    auto function = reinterpret_cast<Function>(version_export("VerInstallFileW"));
    return function ? function(flags, source, destination, sourceDirectory, destinationDirectory,
                               currentDirectory, temporary, length) : VIF_CANNOTREADSRC;
}

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        gModule = module;
        if (supported_host()) arm_entry();
    }
    return TRUE;
}
