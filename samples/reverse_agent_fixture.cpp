#include <iostream>
#include <string>

// Build switches intentionally exercise the analyzer's macro evidence path.
#define RA_ENABLE_TRACE 1
#define RA_MAX_RETRIES 3
#define RA_DISPATCH(mask, bit) \
    (((mask) & (bit)) != 0)

#if defined(RA_LINUX) && !defined(RA_TEST)
#define RA_PLATFORM "linux"
#else
#define RA_PLATFORM "portable"
#endif

using CompletionCallback = void (*)(const std::string&);

struct EventState {
    CompletionCallback completion_callback;
};

void write_audit_log(const std::string& message) {
    std::cout << "audit: " << message << '\n';
}

void persist_result(const std::string& payload) {
    std::cout << "saved: " << payload << '\n';
}

void notify_user(const std::string& payload) {
    std::cout << "notification: " << payload << '\n';
}

void report_failure(const std::string& payload) {
    std::cout << "failure: " << payload << '\n';
}

void register_completion(CompletionCallback callback,
                         const std::string& payload) {
    if (callback != nullptr) {
        callback(payload);
    }
}

void enqueue_background(CompletionCallback callback,
                        const std::string& payload) {
    // The production agent would hand this work to an executor.
    if (callback != nullptr) {
        callback(payload);
    }
}

void dispatch_event_loop(EventState& state, const std::string& payload) {
    if (state.completion_callback != nullptr) {
        state.completion_callback(payload);
    }
}

void process_request(const std::string& request) {
    CompletionCallback handler = &persist_result;
    handler(request);

    EventState state{nullptr};
    state.completion_callback = notify_user;

#if RA_ENABLE_TRACE
    write_audit_log(request);
#endif

    auto audit_task = [](const std::string& payload) {
        write_audit_log(payload);
    };
    audit_task(request);

    register_completion(&persist_result, request);
    enqueue_background(&notify_user, request);
    dispatch_event_loop(state, request);

    if (RA_DISPATCH(RA_MAX_RETRIES, 1)) {
        report_failure("retry budget available");
    }
}

int main() {
    process_request("order-created");
    return 0;
}
