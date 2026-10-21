/* Host-thread and kernel synchronization tests, using the production dispatcher. */
#include "../runtime/runtime.h"
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <mach/mach_time.h>
static atomic_uint calls,environments;
_Noreturn void runtime_fail(const char *message){fprintf(stderr,"FAIL %s\n",message);exit(1);}
void *runtime_symbol(const char *name){(void)name;runtime_fail("unexpected guest symbol");}
void runtime_thread_init(void){atomic_fetch_add(&environments,1);}
uint64_t runtime_now_ns(void){mach_timebase_info_data_t t;mach_timebase_info(&t);return (uint64_t)((__uint128_t)mach_absolute_time()*t.numer/t.denom);}
static void need(int c,const char *m){if(!c)runtime_fail(m);}
static void kernel(unsigned id,uint64_t *r){need(svc_thread((int)id,r)==1,"service dispatch");}
static void child(uint64_t arg){atomic_fetch_add(&calls,1);uint64_t r[31]={arg};kernel(0x11,r);need(r[0]==0,"child signal");kernel(0x0a,r);runtime_fail("ExitThread returned");}
/* BOTW preemption: a priority-44 worker polls in guest code without an SVC on core 1 while a priority-30 thread pinned
   there waits (threads.c core_force). The spinner counts as guest text, so it may be stopped anywhere in its loop. */
#ifdef NSW_GAME_BOTW
#include <unistd.h>
static atomic_int spinning,stop_spin,spinner_resumed;static atomic_ullong urgent_ran_ns;
static void spinner(uint64_t arg){(void)arg;atomic_store(&spinning,1);while(!atomic_load_explicit(&stop_spin,memory_order_acquire)){}
 atomic_store(&spinner_resumed,1);uint64_t r[31]={0};kernel(0x0a,r);}
static atomic_int hi_spinning,stop_hi,hi_resumed;
void runtime_core_checkpoint(void);
/* One SVC after taking its core, then polling (a job worker back from a job): lent once a whole timeslice passes without one. */
static void hi_spinner(uint64_t arg){(void)arg;runtime_core_checkpoint();atomic_store(&hi_spinning,1);while(!atomic_load_explicit(&stop_hi,memory_order_acquire)){}
 atomic_store(&hi_resumed,1);uint64_t r[31]={0};kernel(0x0a,r);}
/* Stress: two threads of different priority pinned to each core, mixing ~3 ms polls without an SVC (pure guest arithmetic,
   so forced preemption applies), yields and short sleeps; every thread must finish every iteration. */
static atomic_uint stress_done;static atomic_ullong stress_iters;
static void stress_worker(uint64_t arg){
 uint32_t seed=(uint32_t)arg*2654435761u+1;uint64_t r[31];
 for(int i=0;i<200;i++){
  seed=seed*1103515245u+12345u;uint64_t n=(seed>>16)%3==0?3000000:30000;
  for(volatile uint64_t k=0;k<n;k++){}
  r[0]=(seed>>20)&1?0:20000;kernel(0x0b,r);atomic_fetch_add(&stress_iters,1);
 }
 atomic_fetch_add(&stress_done,1);uint64_t x[31]={0};kernel(0x0a,x);}
int runtime_guest_text(uint64_t pc){return (pc>=(uintptr_t)spinner&&pc<(uintptr_t)spinner+256)||(pc>=(uintptr_t)hi_spinner&&pc<(uintptr_t)hi_spinner+256)||
 (pc>=(uintptr_t)stress_worker&&pc<(uintptr_t)stress_worker+512);}
static void lender(uint64_t arg){(void)arg;atomic_store_explicit(&stop_hi,1,memory_order_release);uint64_t r[31]={0};kernel(0x0a,r);}
static void urgent(uint64_t arg){(void)arg;atomic_store(&urgent_ran_ns,runtime_now_ns());atomic_store_explicit(&stop_spin,1,memory_order_release);uint64_t r[31]={0};kernel(0x0a,r);}
static uint32_t start_pinned(void (*entry)(uint64_t),int prio,int core){
 uint8_t *stack=mmap(NULL,2<<20,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);need(stack!=MAP_FAILED,"guest stack");
 uint64_t r[31]={0};r[1]=(uintptr_t)entry;r[3]=(uintptr_t)stack+(2<<20);r[4]=(uint64_t)prio;r[5]=(uint64_t)core;kernel(8,r);need(!r[0],"thread create");
 uint32_t h=(uint32_t)r[1];r[0]=h;kernel(9,r);need(!r[0],"thread start");return h;}
static int preemption(void){
 uint32_t spin=start_pinned(spinner,44,1);while(!atomic_load(&spinning))usleep(100);
 uint64_t t0=runtime_now_ns();uint32_t hi=start_pinned(urgent,30,1);uint64_t r[31]={0};
 r[1]=(uintptr_t)&hi;r[2]=1;r[3]=1000000000;kernel(0x18,r);need(!r[0],"higher-priority thread ran beside a spinning owner");
 need(atomic_load(&urgent_ran_ns)-t0<50000000,"preempted within 50 ms");
 r[1]=(uintptr_t)&spin;r[2]=1;r[3]=1000000000;kernel(0x18,r);need(!r[0]&&atomic_load(&spinner_resumed),"preempted spinner resumed");
 return 1;
}
/* The BOTW timeslice (on unless BOTW_CORE_TIMESLICE_MS=0): a priority-30 owner polling without an SVC lends core 2 to a priority-44 thread. */
static int timeslice(void){
 const char *e=getenv("BOTW_CORE_TIMESLICE_MS");if(e&&atof(e)<=0)return 0;
 uint32_t hi=start_pinned(hi_spinner,30,2);while(!atomic_load(&hi_spinning))usleep(100);
 uint32_t lo=start_pinned(lender,44,2);uint64_t r[31]={0};
 r[1]=(uintptr_t)&lo;r[2]=1;r[3]=1000000000;kernel(0x18,r);need(!r[0],"lower-priority thread ran beside a polling owner");
 r[1]=(uintptr_t)&hi;r[2]=1;r[3]=1000000000;kernel(0x18,r);need(!r[0]&&atomic_load(&hi_resumed),"polling owner got its core back");
 return 1;
}
void runtime_core_wait_take(uint64_t *sum_ns,uint64_t *max_ns,uint64_t *forced);
static uint64_t stress(void){
 uint64_t sum,max,forced;runtime_core_wait_take(&sum,&max,&forced);
 static const int prio[6]={30,44,35,48,44,50};uint32_t h[6];
 for(int i=0;i<6;i++)h[i]=start_pinned(stress_worker,prio[i],i/2);
 for(int i=0;i<6;i++){uint64_t r[31]={0};r[1]=(uintptr_t)&h[i];r[2]=1;r[3]=20000000000ull;kernel(0x18,r);need(!r[0],"stress thread finished");}
 need(atomic_load(&stress_done)==6&&atomic_load(&stress_iters)==1200,"every stress iteration ran");
 runtime_core_wait_take(&sum,&max,&forced);need(forced>0,"stress exercised forced preemption");return forced;
}
#else
static int preemption(void){return 0;}
static int timeslice(void){return 0;}
static uint64_t stress(void){return 0;}
#endif
int main(void){
 uint32_t main=svc_thread_main_init();uint64_t r[31]={0};r[1]=main;kernel(0xc,r);need(r[0]==0&&r[1]==44,"initial priority");
 r[0]=main;r[1]=31;kernel(0xd,r);r[1]=main;kernel(0xc,r);need(r[0]==0&&r[1]==31,"priority roundtrip");
 r[1]=7;kernel(0xc,r);need(r[0]==0xe401,"bad handle query");r[0]=7;kernel(0x16,r);need(r[0]==0xe401,"bad handle close");
 unsigned count=0;
 for(unsigned i=0;i<32;i++){
  kernel(0x45,r);need(!r[0],"event create");uint32_t write=(uint32_t)r[1],read=(uint32_t)r[2];
  r[0]=read;kernel(0x11,r);need(r[0]==0xe401,"reject signaling read handle");
  r[1]=(uintptr_t)&read;r[2]=1;r[3]=0;kernel(0x18,r);need(r[0]==0xea01,"unsignaled event timeout");
  uint8_t *stack=mmap(NULL,2<<20,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);need(stack!=MAP_FAILED,"guest stack");
  r[1]=(uintptr_t)child;r[2]=write;r[3]=(uintptr_t)stack+(2<<20);r[4]=44;r[5]=0;kernel(8,r);need(!r[0],"thread create");uint32_t thread=(uint32_t)r[1];
  unsigned before=atomic_load(&calls);r[0]=thread;kernel(9,r);need(!r[0],"thread start");
  r[1]=(uintptr_t)&read;r[2]=1;r[3]=1000000000;kernel(0x18,r);need(!r[0]&&r[1]==0,"event wait");
  r[1]=(uintptr_t)&thread;r[2]=1;r[3]=1000000000;kernel(0x18,r);need(!r[0],"wait thread exit");need(atomic_load(&calls)==before+1,"child ran exactly once");
  r[0]=read;kernel(0x17,r);need(!r[0],"event reset");r[0]=read;kernel(0x17,r);need(r[0]==0xfa01,"second reset fails");
  r[0]=thread;kernel(0x16,r);need(!r[0],"close thread");r[0]=read;kernel(0x16,r);need(!r[0],"close read");r[0]=write;kernel(0x16,r);need(!r[0],"close write");
  need(munmap(stack,2<<20)==0,"reclaim guest stack after exit wait");
  count++;
 }
 need(atomic_load(&environments)==32,"per-thread setup");
 int preempted=preemption(),sliced=timeslice();uint64_t forced=stress();
 printf("{\"status\":\"pass\",\"native_threads\":%u,\"events\":%u,\"preemption\":%s,\"timeslice\":%s,\"stress_preemptions\":%llu,\"scope\":\"production SVC dispatcher, host stack switching, event/thread waits, priority and invalid handles\"}\n",count,count,preempted?"true":"false",sliced?"true":"false",(unsigned long long)forced);return 0;
}
