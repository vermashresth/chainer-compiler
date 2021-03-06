#include "data_iterator.h"

#include <common/log.h>

DataIterator::DataIterator(int buf_size) : buf_size_(buf_size) {
}

DataIterator::~DataIterator() {
    Terminate();
}

std::vector<chainerx::Array> DataIterator::GetNext() {
    std::unique_lock<std::mutex> lock{mu_};
    CHECK(thread_.get());
    while (buf_.empty()) {
        if (is_iteration_finished_) return {};
        cond_.wait(lock);
    }
    auto ret = buf_.front();
    buf_.pop();
    cond_.notify_all();
    return ret;
}

void DataIterator::Start() {
    std::unique_lock<std::mutex> lock{mu_};
    thread_.reset(new std::thread([this]() { Loop(); }));
}

void DataIterator::Terminate() {
    std::unique_lock<std::mutex> lock{mu_};
    CHECK(thread_.get());
    if (should_finish_) return;
    should_finish_ = true;
    cond_.notify_all();
    cond_.wait(lock);
    thread_->join();
}

void DataIterator::Loop() {
    while (true) {
        auto next = GetNextImpl();

        std::unique_lock<std::mutex> lock{mu_};
        if (next.empty()) {
            is_iteration_finished_ = true;
            cond_.notify_all();
            while (!should_finish_) {
                cond_.wait(lock);
                cond_.notify_all();
            }
            return;
        }
        if (should_finish_) {
            cond_.notify_all();
            return;
        }
        while (buf_.size() == buf_size_) {
            cond_.wait(lock);
            if (should_finish_) {
                cond_.notify_all();
                return;
            }
        }
        buf_.push(next);
        cond_.notify_all();
    }
}
