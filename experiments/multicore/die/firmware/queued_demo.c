#include "platform.h"
#include "queue.h"
#include "../../device/task_abi.h"
#include "coral_image.h"
#ifdef NCORAL_ZVT_FIRMWARE
#include "zvt_image.h"
#endif

#define DMA_BASE 0x50000100u
#define STATS (SHARED_BASE+0x3e00u)
enum { DMA_CTRL=0, DMA_STATUS=4, DMA_SRC=8, DMA_DST=12, DMA_BYTES=16,
       DMA_TAG=20, DMA_DONE_TAG=24, DMA_ERROR=28, DMA_PROGRESS=32, DMA_COMPLETED=36 };
static NcQueue queue;
static unsigned max_overlap;
static unsigned inject_busy;
unsigned runtime_memory_test(void);

static int start(void *context,const NcTask *t) {
  (void)context;
  if(t->kind==NC_COPY) {
    if(read32(DMA_BASE+DMA_STATUS)&1)return 1;
    write32(DMA_BASE+DMA_SRC,t->src);write32(DMA_BASE+DMA_DST,t->dst);
    write32(DMA_BASE+DMA_BYTES,t->bytes);write32(DMA_BASE+DMA_TAG,t->signal);
    memory_fence();write32(DMA_BASE+DMA_CTRL,1);
    if(inject_busy && t->bytes>=64) {
      /* Active registers must be a snapshot; a second START must be rejected. */
      write32(DMA_BASE+DMA_CTRL,1);
      write32(DMA_BASE+DMA_SRC,0x60000000);
      write32(DMA_BASE+DMA_TAG,0xdead);
      inject_busy=0;
    }
    return 0;
  }
  if(t->kernel_id!=1 && t->kernel_id!=NC_KERNEL_ZVT)return NC_INVALID;
  unsigned rank=0;
  for(unsigned core=0;core<4;++core)if(t->core_mask&(1u<<core)) {
    write32(coral_address(core,CLUSTER_CSR_ADDRESS),3);
    const uint32_t done=SHARED_BASE+0xc00+core*4;
    write32(done,0);
    if(t->kernel_id==NC_KERNEL_ZVT) {
      const NcKernelArgs args={NC_KERNEL_ABI,sizeof(NcKernelArgs),
        {t->program_id+rank,0,0},{t->grid,1,1},core,
        {t->args[0],t->args[1],t->args[2]},{t->args[3],0,0,0},done};
      const uint32_t *p=(const uint32_t *)&args;
      for(unsigned i=0;i<sizeof(args)/4;++i)write32(coral_address(core,NC_ARGS_ADDRESS+4*i),p[i]);
    } else {
      const uint32_t profile=PROFILE_BASE+core*PROFILE_STRIDE;
      write32(profile,0);
      const ClusterArgs args={t->args[0],t->args[1],t->args[2],t->args[3],done,core,profile,0};
      const uint32_t *p=(const uint32_t *)&args;
      for(unsigned i=0;i<sizeof(args)/4;++i)write32(coral_address(core,CLUSTER_ARGS_ADDRESS+4*i),p[i]);
    }
    ++rank;
  }
  coral_start_mask(t->core_mask);
  return 0;
}
static int poll(void *context,const NcTask *t) {
  (void)context;
  if(t->kind==NC_COPY) {
    unsigned status=read32(DMA_BASE+DMA_STATUS);
    if(status&1)return 0;
    if(!(status&2) || read32(DMA_BASE+DMA_DONE_TAG)!=t->signal)return -20;
    if(status&4)return -(30+(int)read32(DMA_BASE+DMA_ERROR));
    return read32(DMA_BASE+DMA_PROGRESS)==t->bytes?1:-21;
  }
  if(read32(SYSTEM_BASE+SYS_FAULT)&t->core_mask)return -40;
  unsigned done=0;
  for(unsigned core=0;core<4;++core)if(t->core_mask&(1u<<core)) {
    if(read32(coral_address(core,CLUSTER_CSR_ADDRESS+0x10c)))return -41;
    if(read32(SHARED_BASE+0xc00+4*core)==1)done|=1u<<core;
  }
  return (done&t->core_mask)==t->core_mask &&
         (read32(SYSTEM_BASE+SYS_WFI)&t->core_mask)==t->core_mask?1:0;
}
static uint32_t reserve(void) {
  uint32_t token;
  int result=nc_event_reserve(&queue,&token);
  if(result){write32(SYSTEM_BASE+SYS_PHASE,0x54000000u+(unsigned)(-result));die_finish(DIE_BAD_RESULT);}
  return token;
}
static void submit(NcTask *task) {
  int result=nc_submit(&queue,task);
  if(result){write32(SYSTEM_BASE+SYS_PHASE,0x51000000u+(unsigned)(-result));die_finish(DIE_BAD_RESULT);}
}
static void copy(uint32_t src,uint32_t dst,unsigned bytes,uint32_t wait,uint32_t signal) {
  NcTask t={0};t.kind=NC_COPY;t.src=src;t.dst=dst;t.bytes=bytes;t.wait[0]=wait;t.signal=signal;submit(&t);
}
static int av(unsigned core,unsigned i){return (int)((core*3+i)%13)-6;}
static int bv(unsigned core,unsigned i){return (int)((core*2+i)%11)-5;}

void queued_main(unsigned scenario,unsigned n,unsigned mode,uint32_t timeout) {
  if((scenario!=5 && scenario!=6) || n<1 || n>128 || mode>3 || (mode==3 && (scenario!=5 || n<64)))die_finish(DIE_BAD_CONFIG);
  write32(SYSTEM_BASE+SYS_PHASE,0x50000001);
  nc_queue_init(&queue);
  write32(SYSTEM_BASE+SYS_PHASE,0x50000002);
  unsigned memory_error=runtime_memory_test();
  if(memory_error){write32(SYSTEM_BASE+SYS_PHASE,0x57000000u+memory_error);die_finish(DIE_BAD_RESULT);}
  inject_busy=mode==3;
  if(scenario==6) {
#ifdef NCORAL_ZVT_FIRMWARE
    coral_load(zvt_image,ZVT_IMAGE_WORDS,ZVT_IMAGE_ENTRY);
#else
    die_finish(DIE_BAD_CONFIG);
#endif
  }
  else coral_load(coral_image,CORAL_IMAGE_WORDS,CORAL_IMAGE_ENTRY);
  if(scenario==6) {
    for(unsigned core=0;core<4;++core)for(unsigned w=0;w<16;++w) {
      uint32_t a=0,b=0;
      for(unsigned j=0;j<4;++j){a|=(uint32_t)(uint8_t)av(core,4*w+j)<<(8*j);b|=(uint32_t)(uint8_t)bv(core,4*w+j)<<(8*j);}
      write32(SHARED_BASE+core*64+4*w,a);write32(SHARED_BASE+0x400+core*64+4*w,b);
    }
    for(unsigned i=0;i<1024;++i)write32(SHARED_BASE+0x1800+4*i,0xdeadbeef);
    write32(SHARED_BASE+0x17fc,0xa5a5a5a5);write32(SHARED_BASE+0x2800,0x5a5a5a5a);
  } else {
    for(unsigned i=0;i<n;++i){write32(SHARED_BASE+4*i,3*i+1);write32(SHARED_BASE+0x400+4*i,7*i+2);write32(SHARED_BASE+0x800+4*i,0xdeadbeef);}
    if(mode==3)for(unsigned i=n;i<256;++i)write32(SHARED_BASE+4*i,0);
    write32(SHARED_BASE+0x7fc,0xa5a5a5a5);write32(SHARED_BASE+0x800+4*n,0x5a5a5a5a);
  }
  write32(SYSTEM_BASE+SYS_PHASE,0x50000003);
  /* Each core has a two-input copy -> compute -> output copy dependency chain.
   * The scheduler may overlap DMA with compute on a different core. */
  for(unsigned core=0;core<4;++core) {
    unsigned first=core*(n/4)+(core<n%4?core:n%4),count=n/4+(core<n%4);
    uint32_t ea=reserve(),eb=reserve(),ek=reserve(),ec=reserve();
    if(scenario==6 || count) {
      copy(SHARED_BASE+(scenario==6?64*core:4*first),coral_address(core,0x11000),
           mode==3 && core==0?1024:(scenario==6?64:4*count),0,ea);
      copy(SHARED_BASE+0x400+(scenario==6?64*core:4*first),coral_address(core,0x11400),scenario==6?64:4*count,0,eb);
    }
    NcTask t={0};t.kind=NC_KERNEL;t.core_mask=1u<<core;t.kernel_id=scenario==6?NC_KERNEL_ZVT:1;
    t.program_id=core;t.grid=4;t.args[0]=0x11000;t.args[1]=0x11400;t.args[2]=0x11800;t.args[3]=count;
    if(scenario==6 || count){t.wait[0]=ea;t.wait[1]=eb;}
    t.signal=ek;submit(&t);
    if(scenario==6 || count)copy(coral_address(core,0x11800),SHARED_BASE+(scenario==6?0x1800+1024*core:0x800+4*first),scenario==6?1024:4*count,ek,ec);
  }
  write32(SYSTEM_BASE+SYS_PHASE,0x50000004);
  const NcBackend backend={0,start,poll};
  uint32_t beginning=read32(SYSTEM_BASE+SYS_CYCLES);
  while(queue.count) {
    int result=nc_pump(&queue,&backend);
    if(result<0){write32(SYSTEM_BASE+SYS_PHASE,0x52000000u+(unsigned)(-result));die_finish(DIE_BAD_RESULT);}
    if(queue.dma_busy && queue.cores_busy) {
      unsigned busy=0;
      for(unsigned i=0;i<queue.count;++i) {
        NcEntry *e=&queue.entries[(queue.head+i)%NC_QUEUE_CAPACITY];
        if(e->state==NC_RUNNING && e->task.kind==NC_KERNEL)++busy;
      }
      if(busy>max_overlap)max_overlap=busy;
    }
    if(read32(SYSTEM_BASE+SYS_CYCLES)-beginning>=timeout)die_finish(DIE_WAIT_TIMEOUT);
  }
  if(queue.failed) {
    write32(SYSTEM_BASE+SYS_PHASE,0x53000000u|(queue.failed<<8)|queue.completed);
    write32(SYSTEM_BASE+SYS_CHECKSUM,(queue.count<<24)|(queue.submitted<<16)|(queue.events[0].state<<8)|(uint32_t)(-queue.events[0].error));
    for(unsigned i=0;i<NC_EVENT_CAPACITY;++i)if(queue.events[i].state==NC_ERROR && queue.events[i].error!=NC_DEPENDENCY) {
      write32(SYSTEM_BASE+SYS_PHASE,0x53000000u+(unsigned)(-queue.events[i].error));break;
    }
    die_finish(DIE_BAD_RESULT);
  }
  write32(SYSTEM_BASE+SYS_PHASE,0x50000005);
  uint32_t checksum=0;
  if(scenario==6) {
    for(unsigned core=0;core<4;++core)for(unsigned i=0;i<16;++i)for(unsigned j=0;j<16;++j) {
      uint32_t value=read32(SHARED_BASE+0x1800+1024*core+4*(16*i+j));
      int expected=0;
      for(unsigned k=0;k<4;++k)expected+=av(core,16*k+i)*bv(core,16*k+j);
      if(value!=(uint32_t)expected)die_finish(DIE_BAD_RESULT);
      checksum+=value;
    }
  } else {
    for(unsigned i=0;i<n;++i){uint32_t v=read32(SHARED_BASE+0x800+4*i);if(v!=10*i+3)die_finish(DIE_BAD_RESULT);checksum+=v;}
  }
  /* Negative descriptor tests must fail without mutating memory or hanging. */
  if(mode==1 || mode==2) {
    write32(DMA_BASE+DMA_SRC,SHARED_BASE);
    write32(DMA_BASE+DMA_DST,mode==1?0x60000000u:SHARED_BASE+4);
    write32(DMA_BASE+DMA_BYTES,16);write32(DMA_BASE+DMA_TAG,0x1234);write32(DMA_BASE+DMA_CTRL,1);
    unsigned expected=mode==1?2:1;
    if((read32(DMA_BASE+DMA_STATUS)&7)!=6 || read32(DMA_BASE+DMA_ERROR)!=expected)die_finish(DIE_BAD_RESULT);
    write32(DMA_BASE+DMA_CTRL,2);
  }
  write32(STATS,queue.submitted);write32(STATS+4,queue.completed);write32(STATS+8,queue.failed);
  write32(STATS+12,read32(DMA_BASE+DMA_COMPLETED));write32(STATS+16,max_overlap);write32(STATS+20,queue.high_water);
  write32(SYSTEM_BASE+SYS_CHECKSUM,checksum);die_finish(DIE_PASS);
}
