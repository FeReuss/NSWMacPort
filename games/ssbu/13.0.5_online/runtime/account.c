/* Local user identity. Handles are validated; profile survives restarts. Online identity: oof_client.m. */
#include "runtime.h"
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <ctype.h>
typedef struct{uint64_t lo,hi;} Uid;
typedef struct{Uid uid;uint64_t token;} UserHandle;
static Uid local;
/* Canaries around the lock: an intermittent hang showed this mutex's internal words changed while nobody held it. */
#define GUARD 0x6163636f756e7447ULL
static struct{uint64_t pre[8];pthread_mutex_t m;uint64_t post[8];} guarded={{GUARD,GUARD,GUARD,GUARD,GUARD,GUARD,GUARD,GUARD},PTHREAD_MUTEX_INITIALIZER,{GUARD,GUARD,GUARD,GUARD,GUARD,GUARD,GUARD,GUARD}};
#define lock_ guarded.m
static _Atomic(uint64_t) lock_owner;
static void check_guard(void){for(unsigned i=0;i<8;i++)if(guarded.pre[i]!=GUARD||guarded.post[i]!=GUARD){fprintf(stderr,"[totk] account lock guard %s[%u] overwritten: %#llx\n",guarded.pre[i]!=GUARD?"pre":"post",i,(unsigned long long)(guarded.pre[i]!=GUARD?guarded.pre[i]:guarded.post[i]));runtime_fail("host memory near the account lock was overwritten");}}
static void lock_acquire(void){check_guard();pthread_mutex_lock(&lock_);uint64_t t;pthread_threadid_np(NULL,&t);lock_owner=t;}
static void lock_release(void){lock_owner=0;pthread_mutex_unlock(&lock_);check_guard();}
uint64_t runtime_account_lock_owner(void){const uint32_t *w=(const uint32_t *)(const void *)&guarded;fprintf(stderr,"[totk-dump] account lock with guards @%p:",(void *)&guarded);for(unsigned i=0;i<sizeof(guarded)/4;i++)fprintf(stderr," %08x",w[i]);fputc('\n',stderr);return lock_owner;}
static uint64_t tokens[64],nextToken=1;
static void initialize(void){
 lock_acquire();if(local.lo||local.hi){lock_release();return;}
 const char *dir=getenv("TOTK_PROFILE_DIR");if(!dir)dir="user-data";
 if(mkdir(dir,0700)&&errno!=EEXIST)runtime_fail("create local account directory");
 char path[4096];if(snprintf(path,sizeof(path),"%s/account.uid",dir)>=(int)sizeof(path))runtime_fail("local account path length");
 int fd=open(path,O_RDWR|O_CREAT|O_NOFOLLOW,0600);if(fd<0||flock(fd,LOCK_EX))runtime_fail("open local account identity");
 struct stat st;if(fstat(fd,&st)||!S_ISREG(st.st_mode))runtime_fail("invalid local account identity file");
 if(st.st_size==0){do{arc4random_buf(&local,sizeof(local));}while(!local.lo&&!local.hi);if(write(fd,&local,sizeof(local))!=sizeof(local)||fsync(fd))runtime_fail("persist local account identity");}
 else if(st.st_size!=sizeof(local)||pread(fd,&local,sizeof(local),0)!=sizeof(local)||(!local.lo&&!local.hi))runtime_fail("corrupt local account identity");
 close(fd);lock_release();fprintf(stderr,"[totk] persistent local account initialized\n");
}
static bool same(const Uid *uid){return uid&&uid->lo==local.lo&&uid->hi==local.hi;}
static bool valid(const UserHandle *handle){if(!handle||!same(&handle->uid))return false;for(unsigned i=0;i<64;i++)if(tokens[i]&&tokens[i]==handle->token)return true;return false;}
static unsigned preselected(UserHandle *out){if(!out)runtime_fail("account null user handle");initialize();lock_acquire();for(unsigned i=0;i<64;i++)if(!tokens[i]){tokens[i]=nextToken++;*out=(UserHandle){local,tokens[i]};lock_release();return 1;}lock_release();return 0;}
/* Odyssey calls the Result-returning variant after Initialize. The SDK variant
   expects Switch account state that the native local-account host does not use. */
static uint32_t open_preselected(UserHandle *out){return preselected(out)?0:0x7c67;}
static uint32_t user_id(Uid *out,const UserHandle *handle){lock_acquire();bool ok=out&&valid(handle);if(ok)*out=local;lock_release();return ok?0:0x7c67;}
static uint32_t last_opened_user(Uid *out){if(!out)return 0x7c67;initialize();*out=local;return 0;}
static void close_user(const UserHandle *handle){lock_acquire();if(!valid(handle))runtime_fail("invalid local account handle close");for(unsigned i=0;i<64;i++)if(tokens[i]==handle->token){tokens[i]=0;break;}lock_release();}
static uint32_t existence(unsigned char *out,const Uid *uid){if(!out||!uid)return 0x7c67;initialize();*out=same(uid);return 0;}
/* Online on an ssbu-oof server the nickname is the account's user name (standard v0.2 section 2; the launcher passes it as
   OOF_USERNAME), so other players see it; offline, or with a name the server could not have issued, "Local". */
static const char *oof_nickname(void){const char *n=getenv("OOF_USERNAME");if(!n||!runtime_oof_enabled())return NULL;size_t l=strlen(n);if(l<3||l>10)return NULL;for(size_t i=0;i<l;i++)if(!isalnum((unsigned char)n[i])&&!strchr("_-.",n[i]))return NULL;return n;}
static uint32_t nickname(char *out,const Uid *uid){initialize();if(!out||!same(uid))return 0x7c67;memset(out,0,33);const char *n=oof_nickname();if(!n)n="Local";memcpy(out,n,strlen(n));return 0;}
void runtime_account_uid(uint64_t out[2]){initialize();out[0]=local.lo;out[1]=local.hi;}
/* ---- SSBU 13.0.5: user list, and the network service account of the ssbu-oof standard (oof_client.m) ---- */
static uint32_t list_all_users(int *count,Uid *out,int max){if(!count||(max>0&&!out))return 0x7c67;initialize();*count=max>0;if(max>0)out[0]=local;return 0;}
static uint32_t show_user_selector(Uid *out,const void *settings){(void)settings;if(!out)return 0x7c67;initialize();*out=local;return 0;}   /* one local user: no selector */
/* Returned only while no server is configured: the account module's "network service account unavailable". */
#define NSA_UNAVAILABLE (124u|(280u<<9))
/* shortcut: one code for every token failure (the account module's generic network error), until the console's exact
   codes for each failure are observed against a server. */
#define NSA_TOKEN_FAILED (124u|(3000u<<9))
static uint32_t nsa_available(bool *out,const UserHandle *h){lock_acquire();bool ok=valid(h);lock_release();if(!out||!ok)return 0x7c67;*out=runtime_oof_enabled();return 0;}
static uint32_t nsa_ensure(const UserHandle *h){lock_acquire();bool ok=valid(h);lock_release();if(!ok)return 0x7c67;return runtime_oof_enabled()?0:NSA_UNAVAILABLE;}
static uint32_t nsa_id(uint64_t *out,const UserHandle *h){lock_acquire();bool ok=valid(h);lock_release();if(!out||!ok)return 0x7c67;if(!runtime_oof_enabled())return NSA_UNAVAILABLE;*out=runtime_oof_pid();return 0;}
static uint32_t nsa_token_cache(size_t *size,char *buf,size_t cap,const UserHandle *h){
 lock_acquire();bool ok=valid(h);lock_release();if(!size||!ok)return 0x7c67;if(!runtime_oof_enabled())return NSA_UNAVAILABLE;
 size_t n=runtime_oof_token(buf,cap);if(!n)return NSA_TOKEN_FAILED;*size=n;return 0;}
/* Cached Nintendo Account info, laid out as SDK 8.2.1's getters read it (sdk+0x4a7f0..0x4a8e4): birthday at +0x00 (16),
   gender at +0x10 (8), country at +0x20 (4), region at +0x24 (4), each NUL-terminated. Values of the local profile: they
   only change which rules the game shows, never what the server sees. */
static uint32_t cached_account_info(uint8_t *info,const UserHandle *h,void *work,size_t work_size){
 (void)work;(void)work_size;lock_acquire();bool ok=valid(h);lock_release();if(!info||!ok)return 0x7c67;if(!runtime_oof_enabled())return NSA_UNAVAILABLE;
 memset(info,0,0x28);memcpy(info,"2000-01-01",10);memcpy(info+0x10,"unknown",7);
 const char *country=getenv("OOF_COUNTRY");memcpy(info+0x20,country&&strlen(country)==2?country:"US",2);
 return 0;}
/* nn::account::AsyncContext (0x20 bytes; the SDK's own constructors run, +0x18 is the service object on console). Here the
   one asynchronous call finishes before it returns, so +0x18 holds a small host record of its result. */
typedef struct{uint32_t result;uint8_t event[0x100];int bound;} AsyncRecord;
static AsyncRecord **async_slot(void *ctx){return (AsyncRecord **)((uint8_t *)ctx+0x18);}
static uint32_t nsa_token_async(void *ctx,const UserHandle *h){
 lock_acquire();bool ok=valid(h);lock_release();if(!ctx||!ok)return 0x7c67;if(!runtime_oof_enabled())return NSA_UNAVAILABLE;
 AsyncRecord *r=calloc(1,sizeof *r);if(!r)runtime_fail("account async record");
 r->result=runtime_oof_refresh_token()?NSA_TOKEN_FAILED:0;free(*async_slot(ctx));*async_slot(ctx)=r;return 0;}
static uint32_t async_has_done(void *ctx,bool *out){if(!out)return 0x7c67;*out=*async_slot(ctx)!=NULL;return 0;}
static uint32_t async_result(void *ctx){AsyncRecord *r=*async_slot(ctx);return r?r->result:0x7c67;}
static uint32_t async_cancel(void *ctx){(void)ctx;return 0;}   /* already finished */
static uint32_t async_event(void *ctx,void *event){   /* nn::os::SystemEvent* out: bound to a kernel event and set (done) */
 if(!event||!*async_slot(ctx))return 0x7c67;hle_bind_system_event(event,0);hle_signal_system_event(event);return 0;}
static void async_destroy(void *ctx){free(*async_slot(ctx));*async_slot(ctx)=NULL;}
static void *async_assign(void *ctx,void *from){if(ctx!=from){async_destroy(ctx);*async_slot(ctx)=*async_slot(from);*async_slot(from)=NULL;}return ctx;}
void *runtime_account_override(const char *name){
 static const struct{const char *name;void *fn;} entries[]={
 {"_ZN2nn7account10InitializeEv",initialize},{"_ZN2nn7account22TryOpenPreselectedUserEPNS0_10UserHandleE",preselected},
 {"_ZN2nn7account19OpenPreselectedUserEPNS0_10UserHandleE",open_preselected},
 {"_ZN2nn7account17GetLastOpenedUserEPNS0_3UidE",last_opened_user},
 {"_ZN2nn7account9CloseUserERKNS0_10UserHandleE",close_user},{"_ZN2nn7account9GetUserIdEPNS0_3UidERKNS0_10UserHandleE",user_id},
 {"_ZN2nn7account16GetUserExistenceEPbRKNS0_3UidE",existence},{"_ZN2nn7account11GetNicknameEPNS0_8NicknameERKNS0_3UidE",nickname},
 {"_ZN2nn7account12ListAllUsersEPiPNS0_3UidEi",list_all_users},
 {"_ZN2nn7account16ShowUserSelectorEPNS0_3UidERKNS0_21UserSelectionSettingsE",show_user_selector},
 {"_ZN2nn7account32IsNetworkServiceAccountAvailableEPbRKNS0_10UserHandleE",nsa_available},
 {"_ZN2nn7account36EnsureNetworkServiceAccountAvailableERKNS0_10UserHandleE",nsa_ensure},
 {"_ZN2nn7account26GetNetworkServiceAccountIdEPNS0_23NetworkServiceAccountIdERKNS0_10UserHandleE",nsa_id},
 {"_ZN2nn7account37LoadNetworkServiceAccountIdTokenCacheEPmPcmRKNS0_10UserHandleE",nsa_token_cache},
 {"_ZN2nn7account44EnsureNetworkServiceAccountIdTokenCacheAsyncEPNS0_12AsyncContextERKNS0_10UserHandleE",nsa_token_async},
 {"_ZN2nn7account29LoadCachedNintendoAccountInfoEPNS0_25CachedNintendoAccountInfoERKNS0_10UserHandleEPvm",cached_account_info},
 {"_ZN2nn7account12AsyncContext7HasDoneEPb",async_has_done},{"_ZN2nn7account12AsyncContext9GetResultEv",async_result},
 {"_ZN2nn7account12AsyncContext6CancelEv",async_cancel},{"_ZN2nn7account12AsyncContext14GetSystemEventEPNS_2os11SystemEventE",async_event},
 {"_ZN2nn7account12AsyncContextD1Ev",async_destroy},{"_ZN2nn7account12AsyncContextaSEOS1_",async_assign}};
 for(unsigned i=0;i<sizeof(entries)/sizeof(*entries);i++)if(!strcmp(name,entries[i].name))return entries[i].fn;return NULL;
}
