// Host determinism harness: runs a full 12-racer race (CPU-driven, items on,
// seeded input jitter on the local racer) through Kinoko and the app's race
// logic, hashing the complete simulation state after every tick
// (vendor/kinoko/source/game/SimHash.hh). The same binary runs the reference
// physics path and the restructured paths; tools/sim_determinism/run.py
// compares the hash streams. See docs/o3ds-kinoko-arm11.md.
#include <game/SimHash.hh>
#include <game/field/CollisionLane.hh>
#include <game/field/CourseColMgr.hh>
#include <game/field/ObjectDrivableDirector.hh>
#include <game/kart/KartObjectManager.hh>
#include <game/kart/KartMove.hh>
#include <game/system/KPadDirector.hh>
#include <game/system/RaceConfig.hh>
#include <game/system/RaceManager.hh>
#include <egg/core/ExpHeap.hh>
#include <egg/core/SceneManager.hh>
#include <host/SceneCreatorDynamic.hh>
#include <mkw/course_catalog.hpp>
#include <mkw/cpu_ai.hpp>
#include <mkw/garage.hpp>
#include <mkw/race_grid.hpp>
#include <mkw/race_items.hpp>

#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <random>
#include <thread>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

namespace K = Kinoko;
using K::u32;

#if defined(__arm64__) || defined(__aarch64__)
static void setFloatMode() {
    // Like the 3DS main thread: flush-to-zero (FZ) and default NaN (DN), round to nearest.
    uint64_t fpcr;
    asm("mrs %0, fpcr" : "=r"(fpcr));
    fpcr |= (1ull << 24) | (1ull << 25);
    fpcr &= ~(3ull << 22);
    asm volatile("msr fpcr, %0" ::"r"(fpcr));
}
#elif defined(__x86_64__) || defined(__i386__)
#include <immintrin.h>
static void setFloatMode() {
    _MM_SET_FLUSH_ZERO_MODE(_MM_FLUSH_ZERO_ON);
    _MM_SET_DENORMALS_ZERO_MODE(_MM_DENORMALS_ZERO_ON);
}
#else
static void setFloatMode() {}
#endif

namespace {
struct Options {
    unsigned course = 0, seed = 1, ticks = 6000, engineClass = 2, jitter = 1, traceTick = ~0u, chaos = 0;
    std::string path = "reference", out, traceOut;
};
Options g_options;
mkw::RaceGrid *g_raceGrid = nullptr;

// RaceConfig init callback: the same roster rules as src/main.cpp's configure()
// with the local garage choice drawn from the seed.
void configure(K::System::RaceConfig *config, void *context) {
    auto &s = config->raceScenario();
    s.course = mkw::Courses[g_options.course].id;
    s.engineClass = static_cast<K::u8>(g_options.engineClass);
    const unsigned localChoice = (g_options.seed * 2654435761u) % mkw::GarageChoices.size();
    static_cast<mkw::RaceGrid *>(context)->configure(*config, localChoice, 12);
    s.players[0].driftIsAuto = (g_options.seed & 4) != 0;
}

// Seeded input jitter for racer 0 (the "human"): an LCG decides every 20-40
// ticks whether to override the CPU route driver with a random stick, a hop,
// a brake or an item press, so races differ per seed and exercise more code.
struct Jitter {
    uint32_t state;
    unsigned hold = 0;
    uint16_t buttons = 0;
    int stick = 0;
    bool item = false;
    uint32_t next() {
        state = state * 1664525u + 1013904223u;
        return state >> 8;
    }
    void step(bool racing) {
        if (hold) {
            --hold;
            return;
        }
        const uint32_t r = next();
        hold = 20 + (r % 21);
        buttons = 0;
        stick = 0;
        item = false;
        if (!racing || g_options.jitter == 0) {
            return;
        }
        switch ((r >> 8) % 8) {
        case 0: buttons = 1; stick = int((r >> 12) % 15) - 7; break;      // steer hard
        case 1: buttons = 1 | 8; stick = int((r >> 12) % 15) - 7; break;  // hop/drift
        case 2: buttons = 2; break;                                        // brake
        case 3: buttons = 1; item = true; hold = 3; break;                 // use item
        case 4: buttons = 0; break;                                        // coast
        default: break;                                                    // CPU route drives
        }
    }
    bool active() const { return hold != 0 && (buttons || stick); }
};

// Two-thread phase runner with deterministic work stealing: both threads claim
// kart indices from one packed (lo, hi) counter, the main thread from the front
// and the worker from the back, so any kart may run on either thread and the set
// of karts each thread ran is irrelevant to the result. With --chaos N the worker
// sleeps a random 0..N microseconds before every claim, so the interleaving and
// the split vary from tick to tick.
class HostPhaseRunner final : public K::Kart::KartObjectManager::PhaseRunner {
public:
    explicit HostPhaseRunner(unsigned chaos) : m_rng(0x1234567u), m_chaos(chaos) {
        m_thread = std::thread([this] { worker(); });
    }
    ~HostPhaseRunner() {
        m_quit.store(true);
        m_generation.fetch_add(1);
        m_thread.join();
    }
    void run(K::Kart::KartObjectManager &manager, K::Kart::KartObjectManager::Phase phase, u32 begin, u32 end) override {
        m_manager = &manager;
        m_phase = phase;
        m_claims.store(pack(begin, end), std::memory_order_release);
        m_done.store(0, std::memory_order_relaxed);
        m_generation.fetch_add(1, std::memory_order_release); // publishes the batch
        u32 ran = 0;
        for (;;) {
            u32 v = m_claims.load(std::memory_order_acquire);
            u32 lo = v & 0xffff, hi = v >> 16;
            if (lo >= hi) break;
            if (m_claims.compare_exchange_weak(v, pack(lo + 1, hi), std::memory_order_acq_rel)) {
                manager.runPhase(phase, lo);
                ++ran;
            }
        }
        m_done.fetch_add(ran, std::memory_order_acq_rel);
        while (m_done.load(std::memory_order_acquire) < end - begin) {
            // the worker is finishing a kart it claimed
        }
        m_workerRan += (end - begin) - ran;
        m_mainRan += ran;
    }
    unsigned long long mainRan() const { return m_mainRan; }
    unsigned long long workerRan() const { return m_workerRan; }

private:
    static u32 pack(u32 lo, u32 hi) { return lo | (hi << 16); }
    void worker() {
        K::Field::CollisionLane::RegisterWorkerThread();
        setFloatMode();
        u32 seen = 0;
        for (;;) {
            u32 g;
            while ((g = m_generation.load(std::memory_order_acquire)) == seen) {
                std::this_thread::yield();
            }
            seen = g;
            if (m_quit.load()) return;
            u32 ran = 0;
            for (;;) {
                if (m_chaos) {
                    std::this_thread::sleep_for(std::chrono::microseconds(m_rng() % (m_chaos + 1)));
                }
                u32 v = m_claims.load(std::memory_order_acquire);
                u32 lo = v & 0xffff, hi = v >> 16;
                if (lo >= hi) break;
                if (m_claims.compare_exchange_weak(v, pack(lo, hi - 1), std::memory_order_acq_rel)) {
                    m_manager->runPhase(m_phase, hi - 1);
                    ++ran;
                }
            }
            m_done.fetch_add(ran, std::memory_order_acq_rel);
        }
    }
    std::thread m_thread;
    std::atomic<u32> m_generation{0}, m_claims{0}, m_done{0};
    std::atomic<bool> m_quit{false};
    K::Kart::KartObjectManager *m_manager = nullptr;
    K::Kart::KartObjectManager::Phase m_phase{};
    std::minstd_rand m_rng;
    unsigned m_chaos;
    unsigned long long m_mainRan = 0, m_workerRan = 0;
};
} // namespace

int main(int argc, char **argv) {
    for (int i = 1; i + 1 < argc; i += 2) {
        std::string k = argv[i], v = argv[i + 1];
        if (k == "--course") g_options.course = unsigned(std::atoi(v.c_str()));
        else if (k == "--seed") g_options.seed = unsigned(std::atoi(v.c_str()));
        else if (k == "--ticks") g_options.ticks = unsigned(std::atoi(v.c_str()));
        else if (k == "--engine") g_options.engineClass = unsigned(std::atoi(v.c_str()));
        else if (k == "--jitter") g_options.jitter = unsigned(std::atoi(v.c_str()));
        else if (k == "--path") g_options.path = v;
        else if (k == "--out") g_options.out = v;
        else if (k == "--trace-tick") g_options.traceTick = unsigned(std::atoi(v.c_str()));
        else if (k == "--trace-out") g_options.traceOut = v;
        else if (k == "--chaos") g_options.chaos = unsigned(std::atoi(v.c_str()));
        else { std::fprintf(stderr, "unknown option %s\n", k.c_str()); return 2; }
    }
    if (g_options.course >= std::size(mkw::Courses)) {
        std::fprintf(stderr, "course index out of range\n");
        return 2;
    }
    setFloatMode();
    if (!K::Kart::KartObjectManager::SetCalcMode(g_options.path.c_str())) {
        std::fprintf(stderr, "unknown path %s\n", g_options.path.c_str());
        return 2;
    }
    std::unique_ptr<HostPhaseRunner> runner;
    if (g_options.path == "parallel") {
        runner = std::make_unique<HostPhaseRunner>(g_options.chaos);
        K::Kart::KartObjectManager::SetPhaseRunner(runner.get());
    }

    void *memory = std::aligned_alloc(32, K::MEMORY_SPACE_SIZE);
    std::memset(memory, 0, K::MEMORY_SPACE_SIZE);
    auto *heap = K::EGG::ExpHeap::create(memory, K::MEMORY_SPACE_SIZE, K::DEFAULT_OPT);
    heap->setName("EGGRoot");
    heap->becomeCurrentHeap();
    K::EGG::SceneManager::SetRootHeap(heap);

    K::Host::SceneCreatorDynamic creator;
    K::EGG::SceneManager scenes(&creator);
    mkw::RaceGrid raceGrid;
    g_raceGrid = &raceGrid;
    auto raceItems = std::make_unique<mkw::RaceItems>();
    K::System::RaceConfig::RegisterInitCallback(configure, &raceGrid);
    scenes.changeScene(0);
    if (!raceGrid.reset()) {
        std::fprintf(stderr, "course CPU route unavailable\n");
        return 3;
    }
    raceItems->resetFromCourse(raceGrid.count(), 0x4d4b5749u ^ (g_options.seed * 0x9E3779B9u));
    if (!raceItems->oddsReady()) {
        std::fprintf(stderr, "item odds unavailable\n");
        return 3;
    }
    raceGrid.driveLocal = true;
    if (std::getenv("SIM_PRINT_KCL")) {
        const auto *data = K::Field::CourseColMgr::Instance()->data();
        std::fprintf(stderr, "course=%u prisms=%zu normals=%zu vertices=%zu\n", g_options.course, data->prisms().size(),
                data->nrms().size(), data->vertices().size());
    }

    FILE *out = g_options.out.empty() ? nullptr : std::fopen(g_options.out.c_str(), "wb");
    std::vector<K::Hasher::Entry> trace;
    Jitter jitter{0x6a09e667u ^ (g_options.seed * 0x85EBCA6Bu)};
    auto *controller = K::System::KPadDirector::Instance()->hostController();
    uint64_t last = 0;
    unsigned finishedTick = 0;
    for (unsigned tick = 0; tick < g_options.ticks; ++tick) {
        auto *raceMgr = K::System::RaceManager::Instance();
        const bool racing = raceMgr->isStageReached(K::System::RaceManager::Stage::Race);
        // The same per-tick sequence as src/main.cpp's race loop.
        std::array<mkw::ItemInput, 12> inputs{};
        const bool afterGoal = raceGrid.finished();
        raceGrid.driveLocal = true;
        jitter.step(racing && !afterGoal);
        inputs[0] = afterGoal ? mkw::ItemInput{} : raceItems->autoItemInput(0);
        if (jitter.item && !afterGoal) {
            inputs[0].use = true;
        }
        for (unsigned i = 1; i < raceGrid.count(); ++i) {
            if (!raceGrid.ai().ready()) {
                inputs[i] = raceItems->autoItemInput(i);
                continue;
            }
            const auto &c = raceGrid.ai().itemCommand(i);
            inputs[i] = {c.use, c.backwards, c.forwards};
        }
        raceItems->beforeStepNative(inputs.data(), raceGrid.count(), racing, raceGrid.bulletNoEndPlayers());
        for (unsigned i = 0; i < raceGrid.count(); ++i) {
            const auto &h = raceItems->hud(i);
            const bool held = h.deployed && h.heldCharges > 0;
            raceGrid.ai().setItemView(i, {uint8_t(h.item), uint8_t(held ? h.heldCharges : 0), h.roulette != 0, held,
                    held && (h.heldKind == mkw::ItemKind::RedShell || h.heldKind == mkw::ItemKind::TripleRedShell),
                    h.ink && !h.inkDelay});
        }
        {
            std::array<mkw::Vec3, mkw::CpuAi::MaxHazards> hazards{};
            std::array<float, mkw::CpuAi::MaxHazards> radii{};
            unsigned n = 0;
            for (const auto &a : raceItems->actors()) {
                if (n >= hazards.size()) break;
                if (!a.active || a.explosion || !a.settled) continue;
                if (a.kind != mkw::ItemKind::Banana && a.kind != mkw::ItemKind::FakeBox && a.kind != mkw::ItemKind::Bobomb) continue;
                hazards[n] = a.position;
                radii[n] = a.radius;
                ++n;
            }
            raceGrid.ai().setHazards(hazards.data(), radii.data(), n);
        }
        raceGrid.beforeStep(raceItems->bulletPlayers());
        if (jitter.active() && !afterGoal && !(raceItems->bulletPlayers() & 1)) {
            controller->setInputsRawStick(jitter.buttons, unsigned(jitter.stick + 7), 7, K::System::Trick::None);
        }
        scenes.calc();
        raceGrid.afterStep();
        raceItems->afterStepNative(raceGrid.count());

        K::Hasher hasher;
        if (tick == g_options.traceTick) {
            hasher.setTrace(&trace);
        }
        last = K::SimHash::race(hasher);
        // App-side state that feeds back into the simulation: item actors and HUD.
        for (const auto &a : raceItems->actors()) {
            if (!a.active) continue;
            hasher.field("item.kind", &a.kind, sizeof a.kind);
            hasher.field("item.position", &a.position, sizeof a.position);
            hasher.field("item.velocity", &a.velocity, sizeof a.velocity);
            hasher.field("item.age", &a.age, sizeof a.age);
        }
        for (unsigned i = 0; i < raceGrid.count(); ++i) {
            const auto &h = raceItems->hud(i);
            hasher.field("hud.item", &h.item, sizeof h.item);
            hasher.field("hud.heldKind", &h.heldKind, sizeof h.heldKind);
            hasher.field("hud.heldCharges", &h.heldCharges, sizeof h.heldCharges);
        }
        last = hasher.value();
        if (out) {
            std::fwrite(&last, sizeof last, 1, out);
        }
        if (tick == g_options.traceTick && !g_options.traceOut.empty()) {
            if (FILE *tf = std::fopen(g_options.traceOut.c_str(), "w")) {
                for (const auto &e : trace) {
                    std::fprintf(tf, "%s %016llx\n", e.label, static_cast<unsigned long long>(e.hash));
                }
                std::fclose(tf);
            }
        }
        bool allDone = true;
        for (unsigned i = 0; i < raceGrid.count(); ++i) allDone &= raceGrid.finished(i);
        if (allDone && !finishedTick) finishedTick = tick;
        if (finishedTick && tick > finishedTick + 300) break;
    }
    if (out) std::fclose(out);
    auto *raceMgr = K::System::RaceManager::Instance();
    std::printf("course=%u seed=%u path=%s ticks=%u stage=%d lap0=%d finished=%u parallel_ticks=%u serial_ticks=%u",
            g_options.course, g_options.seed, g_options.path.c_str(), g_options.ticks, int(raceMgr->stage()),
            int(raceMgr->player(0).currentLap()), finishedTick, K::Kart::KartObjectManager::s_parallelTicks,
            K::Kart::KartObjectManager::s_serialTicks);
    if (runner) {
        std::printf(" main_karts=%llu worker_karts=%llu", runner->mainRan(), runner->workerRan());
    }
    for (size_t id = 0; id < K::Field::ObjectDrivableDirector::STALE_IDS; ++id) {
        if (K::Field::ObjectDrivableDirector::s_staleTicks[id]) {
            std::printf(" stale[0x%zx]=%u", id, K::Field::ObjectDrivableDirector::s_staleTicks[id]);
        }
    }
    std::printf(" final=%016llx\n", static_cast<unsigned long long>(last));
    K::Kart::KartObjectManager::SetPhaseRunner(nullptr);
    runner.reset();
    return 0;
}
