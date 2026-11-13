#ifndef NCORAL_QUEUE_H_
#define NCORAL_QUEUE_H_
#include <stdint.h>

#define NC_QUEUE_CAPACITY 32u
#define NC_EVENT_CAPACITY 64u
enum { NC_COPY=1, NC_KERNEL=2 };
enum { NC_FREE=0, NC_RESERVED=1, NC_PENDING=2, NC_SUCCESS=3, NC_ERROR=4 };
enum { NC_QUEUED=1, NC_RUNNING=2, NC_DONE=3, NC_FAILED=4 };
enum { NC_INVALID=-1, NC_FULL=-2, NC_STALE=-3, NC_IN_USE=-4,
       NC_DEPENDENCY=-5, NC_DEADLOCK=-6, NC_EXHAUSTED=-7 };

/* Addresses are RV32 device addresses; IDs are logical program IDs, not harts.
 * Event tokens include a generation. Zero means no dependency/completion event.
 */
typedef struct {
  uint32_t kind, core_mask, wait[2], signal;
  uint32_t src, dst, bytes;
  uint32_t kernel_id, program_id, grid, args[4];
} NcTask;
typedef struct { uint32_t token, state, references; int32_t error; } NcEvent;
typedef struct { NcTask task; uint32_t state; int32_t error; } NcEntry;
typedef struct {
  NcEntry entries[NC_QUEUE_CAPACITY];
  NcEvent events[NC_EVENT_CAPACITY];
  uint32_t head, count, generation, cores_busy, dma_busy;
  uint32_t submitted, completed, failed, high_water;
} NcQueue;
typedef struct {
  void *context;
  /* start: 0 accepted, 1 temporarily busy, negative failure.
   * poll: 0 running, 1 complete, negative failure. */
  int (*start)(void *, const NcTask *);
  int (*poll)(void *, const NcTask *);
} NcBackend;

void nc_queue_init(NcQueue *q);
int nc_event_reserve(NcQueue *q, uint32_t *token);
int nc_event_release(NcQueue *q, uint32_t token);
int nc_event_status(NcQueue *q, uint32_t token, int32_t *error);
int nc_submit(NcQueue *q, const NcTask *task);
int nc_pump(NcQueue *q, const NcBackend *backend);
#endif
