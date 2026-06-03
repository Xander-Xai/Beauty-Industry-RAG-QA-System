import time, json, threading 
with open('config.json', encoding='utf-8') as f: config = json.load(f) 
class KVAdmissionControl: 
    KV_PER_TOKEN = 0.45 * 1024 
    OUTPUT_MAP = {'regulation':1024,'formulation':768,'ingredient':512,'general':256} 
    def __init__(self): 
        kv_gb = config['gpu0']['models']['gen_14b']['kv_cache_budget_gb'] 
        sf = config['admission_control']['safety_factor'] 
        self.kv_budget = int(kv_gb * 1024**3 * sf) 
        self.kv_total = int(kv_gb * 1024**3) 
        self.active = {}; self._lock = threading.Lock() 
    def estimate_kv(self, inp, out, bt): 
        if out == 0: out = self.OUTPUT_MAP.get(bt, 512) 
        return (inp + out * 1.2) * self.KV_PER_TOKEN 
    def get_pressure(self): 
        u = sum(self.estimate_kv(v[0],v[1],v[2]) for v in self.active.values()) 
        return u / self.kv_total if self.kv_total > 0 else 0.0 
    def admit(self, rid, inp, out, bt): 
        p = self.get_pressure() 
        if p > 0.95: return False, 'critical' 
        if p > 0.9: return False, 'soft_stop' 
        if p > 0.8: return True, 'admitted_with_pressure' 
        est = self.estimate_kv(inp, out, bt) 
        cur = sum(self.estimate_kv(v[0],v[1],v[2]) for v in self.active.values()) 
        if cur + est > self.kv_budget: return False, 'budget_exceeded' 
        with self._lock: self.active[rid] = (inp, out, bt) 
        return True, 'admitted' 
    def release(self, rid): 
        with self._lock: self.active.pop(rid, None) 
    def get_status(self): 
        return {'kv_pressure': round(self.get_pressure(), 3), 'active': len(self.active)}
