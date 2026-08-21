/* Synthetic Redis-style fixture.  This validates profile rules only; it is
 * deliberately not presented as evidence from the Redis repository. */

#define AE_FILE_EVENTS 1
#define AE_BARRIER (1 << 4)
#define AE_DISPATCH(mask, bit) \
  (((mask) & (bit)) != 0)

#if defined(HAVE_EPOLL) && !defined(HAVE_KQUEUE)
#define AE_BACKEND "epoll"
#endif

typedef struct aeEventLoop aeEventLoop;
typedef void aeFileProc(aeEventLoop*, int, void*, int);
typedef int aeTimeProc(aeEventLoop*, long long, void*);

typedef struct aeFileEvent {
  aeFileProc* rfileProc;
  void* clientData;
} aeFileEvent;

typedef struct aeTimeEvent {
  aeTimeProc* timeProc;
  void* clientData;
} aeTimeEvent;

struct aeEventLoop {
  aeFileEvent events[1];
  aeTimeEvent timeEvent;
};

void readQueryFromClient(aeEventLoop* eventLoop, int fd, void* data, int mask) {
  (void) eventLoop;
  (void) fd;
  (void) data;
  (void) mask;
}

int serverCron(aeEventLoop* eventLoop, long long id, void* data) {
  (void) eventLoop;
  (void) id;
  (void) data;
  return 100;
}

int processTimeEvents(aeEventLoop* eventLoop) {
  return eventLoop->timeEvent.timeProc(eventLoop, 1, eventLoop->timeEvent.clientData);
}

long long aeCreateTimeEvent(aeEventLoop* eventLoop,
                            long long milliseconds,
                            aeTimeProc* proc,
                            void* clientData,
                            void* finalizerProc) {
  (void) milliseconds;
  (void) finalizerProc;
  eventLoop->timeEvent.timeProc = proc;
  eventLoop->timeEvent.clientData = clientData;
  return 1;
}

int aeCreateFileEvent(aeEventLoop* eventLoop,
                      int fd,
                      int mask,
                      aeFileProc* proc,
                      void* clientData) {
  (void) fd;
  (void) mask;
  eventLoop->events[0].rfileProc = proc;
  eventLoop->events[0].clientData = clientData;
  return 0;
}

int aeProcessEvents(aeEventLoop* eventLoop, int flags) {
  (void) flags;
  eventLoop->events[0].rfileProc(eventLoop, 0, 0, AE_FILE_EVENTS);
  processTimeEvents(eventLoop);
  return 1;
}

void aeMain(aeEventLoop* eventLoop) {
  aeProcessEvents(eventLoop, AE_FILE_EVENTS);
}

int main(void) {
  aeEventLoop loop = {0};
  aeCreateFileEvent(&loop, 0, AE_FILE_EVENTS, readQueryFromClient, 0);
  aeCreateTimeEvent(&loop, 100, serverCron, 0, 0);
  aeMain(&loop);
  return 0;
}
