// Separate experimental TOTK cache identity; D2R shader caches are rejected.
#define DMSH_CHAIN_BOTW "botw-native-v1-control-size"
#define DMSH_CHAIN_TOTK "totk-native-v4-unverified"
#ifdef NSW_GAME_BOTW
#define DMSH_CHAIN DMSH_CHAIN_BOTW
#else
#define DMSH_CHAIN DMSH_CHAIN_TOTK
#endif
