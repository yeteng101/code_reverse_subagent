#include <functional>
#include <iostream>
#include <string>
#include <thread>

#define ENABLE_AUDIT 1
#define DEFAULT_RETRY 3

using ResultCallback = void (*)(const std::string&);

void write_audit_log(const std::string& message) {
    std::cout << "audit: " << message << '\n';
}

void persist_result(const std::string& payload) {
    std::cout << "saved: " << payload << '\n';
}

void notify_user(const std::string& payload) {
    std::cout << "notification: " << payload << '\n';
}

void register_completion(ResultCallback callback, const std::string& payload) {
    callback(payload);
}

void dispatch_background(ResultCallback callback, const std::string& payload) {
    std::thread worker(callback, payload);
    worker.join();
}

void process_request(const std::string& request) {
    ResultCallback handler = &persist_result;
    handler(request);

#if ENABLE_AUDIT
    write_audit_log(request);
#endif

    register_completion(&persist_result, request);
    dispatch_background(&notify_user, request);
}

int main() {
    process_request("order-created");
    return 0;
}
