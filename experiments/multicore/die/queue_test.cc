#include <cassert>
#include <cstdio>
#include <map>
extern "C" {
#include "firmware/queue.h"
}
struct Fake {
  unsigned cores=0,dma=0,started=0,overlaps=0;
  std::map<unsigned,unsigned> remaining;
  static int Start(void *p,const NcTask *t) {
    auto &f=*static_cast<Fake *>(p);
    assert(!(f.cores&t->core_mask));
    assert(t->kind!=NC_COPY || !f.dma);
    f.cores|=t->core_mask;f.dma+=t->kind==NC_COPY;++f.started;
    f.remaining[t->signal]=t->args[0]+1;
    if(f.dma && (f.cores&~t->core_mask))++f.overlaps;
    return 0;
  }
  static int Poll(void *p,const NcTask *t) {
    auto &f=*static_cast<Fake *>(p);
    if(--f.remaining[t->signal])return 0;
    f.cores&=~t->core_mask;f.dma-=t->kind==NC_COPY;
    return t->args[1]?-50:1;
  }
};
static unsigned Reserve(NcQueue &q){unsigned t;assert(!nc_event_reserve(&q,&t));return t;}
static NcTask Kernel(unsigned signal,unsigned core=1) {
  NcTask t={};t.kind=NC_KERNEL;t.core_mask=core;t.grid=4;t.signal=signal;t.args[0]=2;return t;
}
static void Drain(NcQueue &q,Fake &f) {
  NcBackend b{&f,Fake::Start,Fake::Poll};
  for(unsigned i=0;q.count && i<1000;++i)assert(nc_pump(&q,&b)>=0);
  assert(!q.count && !q.cores_busy && !q.dma_busy && !f.cores && !f.dma);
}
int main() {
  NcQueue q;Fake f;
  nc_queue_init(&q);
  auto producer=Reserve(q),consumer=Reserve(q);
  auto a=Kernel(producer),b=Kernel(consumer,2);b.wait[0]=producer;
  assert(!nc_submit(&q,&b));assert(!nc_submit(&q,&a));
  assert(nc_event_release(&q,producer)==NC_IN_USE);
  assert(nc_submit(&q,&a)==NC_INVALID);
  Drain(q,f);assert(q.completed==2 && !q.failed);
  assert(nc_event_status(&q,consumer,nullptr)==NC_SUCCESS);
  assert(!nc_event_release(&q,producer));assert(!nc_event_release(&q,consumer));
  auto newer=Reserve(q);assert(newer!=producer);assert(nc_event_status(&q,producer,nullptr)==NC_STALE);
  assert(!nc_event_release(&q,newer));
  for(unsigned round=0;round<8;++round) {
    unsigned events[8];
    for(unsigned i=0;i<8;++i){events[i]=Reserve(q);auto t=Kernel(events[i],1u<<(i%4));assert(!nc_submit(&q,&t));}
    Drain(q,f);for(auto t:events)assert(!nc_event_release(&q,t));
  }
  assert(q.completed==66 && q.head==2);

  nc_queue_init(&q);f=Fake{};
  producer=Reserve(q);consumer=Reserve(q);auto independent=Reserve(q);
  a=Kernel(producer);a.args[1]=1;b=Kernel(consumer,2);b.wait[0]=producer;
  auto c=Kernel(independent,4);
  assert(!nc_submit(&q,&a));assert(!nc_submit(&q,&b));assert(!nc_submit(&q,&c));Drain(q,f);
  assert(q.failed==2 && q.completed==1 && f.started==2);
  int32_t error=0;assert(nc_event_status(&q,consumer,&error)==NC_ERROR && error==NC_DEPENDENCY);

  nc_queue_init(&q);f=Fake{};
  producer=Reserve(q);consumer=Reserve(q);a=Kernel(producer);b=Kernel(consumer,2);
  a.wait[0]=consumer;b.wait[0]=producer;
  assert(!nc_submit(&q,&a));assert(!nc_submit(&q,&b));
  NcBackend backend{&f,Fake::Start,Fake::Poll};assert(nc_pump(&q,&backend)==NC_DEADLOCK);
  a.signal=a.wait[0];assert(nc_submit(&q,&a)==NC_INVALID);

  nc_queue_init(&q);f=Fake{};
  producer=Reserve(q);consumer=Reserve(q);
  a=Kernel(producer,2);a.args[0]=8;
  b={};b.kind=NC_COPY;b.src=0x20000000;b.dst=0x40011000;b.bytes=20;b.signal=consumer;b.args[0]=4;
  assert(!nc_submit(&q,&a));assert(!nc_submit(&q,&b));Drain(q,f);
  assert(f.overlaps && q.completed==2);

  nc_queue_init(&q);
  for(unsigned i=0;i<NC_QUEUE_CAPACITY;++i){a=Kernel(Reserve(q));assert(!nc_submit(&q,&a));}
  a=Kernel(Reserve(q));assert(nc_submit(&q,&a)==NC_FULL);
  nc_queue_init(&q);for(unsigned i=0;i<NC_EVENT_CAPACITY;++i)Reserve(q);
  unsigned token;assert(nc_event_reserve(&q,&token)==NC_FULL);
  nc_queue_init(&q);q.generation=0xffffff;assert(nc_event_reserve(&q,&token)==NC_EXHAUSTED);
  nc_queue_init(&q);a=Kernel(Reserve(q),15);a.program_id=1;assert(nc_submit(&q,&a)==NC_INVALID);
  std::puts("PASS: dependency order, wraparound, generation, resource exclusion, overlap, failure propagation, deadlock, capacity");
}
