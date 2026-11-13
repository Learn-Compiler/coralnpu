#include "queue.h"

static NcEvent *event(NcQueue *q, uint32_t token) {
  unsigned slot=token&255u;
  if(!slot || slot>NC_EVENT_CAPACITY)return 0;
  NcEvent *e=&q->events[slot-1];
  return e->state!=NC_FREE && e->token==token?e:0;
}
void nc_queue_init(NcQueue *q) {
  unsigned char *p=(unsigned char *)q;
  for(unsigned i=0;i<sizeof(*q);++i)p[i]=0;
}
int nc_event_reserve(NcQueue *q, uint32_t *token) {
  if(!token)return NC_INVALID;
  if(q->generation==0xffffffu)return NC_EXHAUSTED;
  for(unsigned i=0;i<NC_EVENT_CAPACITY;++i)if(q->events[i].state==NC_FREE) {
    NcEvent *e=&q->events[i];
    e->token=(++q->generation<<8)|(i+1);
    e->state=NC_RESERVED;e->references=0;e->error=0;*token=e->token;return 0;
  }
  return NC_FULL;
}
int nc_event_release(NcQueue *q, uint32_t token) {
  NcEvent *e=event(q,token);
  if(!e)return NC_STALE;
  if(e->references || e->state==NC_PENDING)return NC_IN_USE;
  e->state=NC_FREE;return 0;
}
int nc_event_status(NcQueue *q, uint32_t token, int32_t *error) {
  NcEvent *e=event(q,token);
  if(!e)return NC_STALE;
  if(error)*error=e->error;
  return (int)e->state;
}
static unsigned endpoint_core(uint32_t address) {
  return address>=0x40000000u && address<0x40400000u ? 1u<<((address>>20)&3):0;
}
int nc_submit(NcQueue *q, const NcTask *task) {
  if(!task || (task->kind!=NC_COPY && task->kind!=NC_KERNEL) || task->core_mask>15)return NC_INVALID;
  if(q->count==NC_QUEUE_CAPACITY)return NC_FULL;
  if(task->kind==NC_KERNEL && (!task->core_mask || !task->grid || task->program_id>=task->grid))return NC_INVALID;
  if(task->kind==NC_KERNEL) {
    unsigned programs=0;
    for(unsigned mask=task->core_mask;mask;mask>>=1)programs+=mask&1u;
    if(task->grid-task->program_id<programs)return NC_INVALID;
  }
  if(task->kind==NC_COPY && (!task->bytes || ((task->src|task->dst|task->bytes)&3)))return NC_INVALID;
  NcEvent *signal=task->signal?event(q,task->signal):0;
  if(task->signal && (!signal || signal->state!=NC_RESERVED))return NC_INVALID;
  for(unsigned i=0;i<2;++i)
    if(task->wait[i] && (!event(q,task->wait[i]) || task->wait[i]==task->signal))return NC_INVALID;
  NcEntry *entry=&q->entries[(q->head+q->count)%NC_QUEUE_CAPACITY];
  entry->task=*task;
  if(task->kind==NC_COPY)entry->task.core_mask=endpoint_core(task->src)|endpoint_core(task->dst);
  entry->state=NC_QUEUED;entry->error=0;
  if(signal){signal->state=NC_PENDING;++signal->references;}
  for(unsigned i=0;i<2;++i)if(task->wait[i])++event(q,task->wait[i])->references;
  ++q->count;++q->submitted;
  if(q->count>q->high_water)q->high_water=q->count;
  return 0;
}
static void finish(NcQueue *q, NcEntry *entry, int error) {
  NcTask *t=&entry->task;
  entry->state=error?NC_FAILED:NC_DONE;entry->error=error;
  if(error)++q->failed;else ++q->completed;
  if(t->signal) {
    NcEvent *e=event(q,t->signal);
    e->state=error?NC_ERROR:NC_SUCCESS;e->error=error;--e->references;
  }
  for(unsigned i=0;i<2;++i)if(t->wait[i])--event(q,t->wait[i])->references;
}
int nc_pump(NcQueue *q, const NcBackend *backend) {
  if(!backend || !backend->start || !backend->poll)return NC_INVALID;
  int progress=0;
  for(unsigned i=0;i<q->count;++i) {
    NcEntry *e=&q->entries[(q->head+i)%NC_QUEUE_CAPACITY];
    if(e->state!=NC_RUNNING)continue;
    int result=backend->poll(backend->context,&e->task);
    if(!result)continue;
    q->cores_busy&=~e->task.core_mask;
    if(e->task.kind==NC_COPY)q->dma_busy=0;
    finish(q,e,result<0?result:0);++progress;
  }
  for(unsigned i=0;i<q->count;++i) {
    NcEntry *e=&q->entries[(q->head+i)%NC_QUEUE_CAPACITY];
    if(e->state!=NC_QUEUED)continue;
    int ready=1,failed=0;
    for(unsigned j=0;j<2;++j)if(e->task.wait[j]) {
      NcEvent *dep=event(q,e->task.wait[j]);
      if(dep->state==NC_ERROR)failed=1;
      if(dep->state!=NC_SUCCESS)ready=0;
    }
    if(failed){finish(q,e,NC_DEPENDENCY);++progress;continue;}
    if(!ready || (q->cores_busy&e->task.core_mask) || (e->task.kind==NC_COPY && q->dma_busy))continue;
    int result=backend->start(backend->context,&e->task);
    if(result==1)continue;
    if(result<0){finish(q,e,result);++progress;continue;}
    q->cores_busy|=e->task.core_mask;
    if(e->task.kind==NC_COPY)q->dma_busy=1;
    e->state=NC_RUNNING;++progress;
  }
  while(q->count) {
    NcEntry *e=&q->entries[q->head];
    if(e->state!=NC_DONE && e->state!=NC_FAILED)break;
    e->state=0;q->head=(q->head+1)%NC_QUEUE_CAPACITY;--q->count;
  }
  if(!progress && q->count && !q->cores_busy && !q->dma_busy) {
    int ready=0;
    for(unsigned i=0;i<q->count;++i) {
      NcEntry *e=&q->entries[(q->head+i)%NC_QUEUE_CAPACITY];
      int dependencies=1;
      for(unsigned j=0;j<2;++j)if(e->task.wait[j] && event(q,e->task.wait[j])->state!=NC_SUCCESS)dependencies=0;
      if(e->state==NC_QUEUED && dependencies)ready=1;
    }
    if(!ready)return NC_DEADLOCK;
  }
  return progress;
}
