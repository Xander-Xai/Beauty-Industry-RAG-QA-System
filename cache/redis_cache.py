import hashlib, json, time 
with open('config.json', encoding='utf-8') as f: config = json.load(f) 
class RedisCache: 
    def __init__(self): 
        self._l1 = {}; self._l1_max = 1000; self.redis_client = None; self.enabled = False 
        try: 
            import redis 
            rc = config['redis']['cache'] 
            self.redis_client = redis.Redis(host=rc['host'],port=rc['port'],db=rc['db'],decode_responses=True,socket_timeout=2) 
            self.redis_client.ping(); self.enabled = True 
        except Exception: self.enabled = False 
    @staticmethod 
    def compute_cache_key(qh, rw, cs, pv, rm, dm): 
        ks = json.dumps({'q':qh,'p':pv,'rm':rm,'dm':dm}, sort_keys=True) 
        return hashlib.sha256(ks.encode()).hexdigest() 
    def get(self, key, rm, dm): 
        if rm == 0 and dm == 0 and key in self._l1: 
            v, exp = self._l1[key] 
            if time.time() < exp: return v 
        if self.enabled and self.redis_client: 
            try: 
                d = self.redis_client.get('rag:l2:' + key) 
                if d: return json.loads(d) 
            except Exception: pass 
        return None 
    def set(self, key, val, rm, dm, ttl=None): 
        if rm == 0 and dm == 0: 
            if len(self._l1) >= self._l1_max: self._l1.pop(next(iter(self._l1))) 
            self._l1[key] = (val, time.time() + config['cache_config']['l1_ttl_seconds']) 
        if self.enabled and self.redis_client: 
            try: self.redis_client.setex('rag:l2:' + key, ttl or config['cache_config']['l2_ttl_seconds'], json.dumps(val, ensure_ascii=False)) 
            except Exception: pass 
    def invalidate_by_epoch(self, new_epoch): 
        self._l1.clear() 
    def get_stats(self): 
        return {'l1_size': len(self._l1), 'l2_enabled': self.enabled}
