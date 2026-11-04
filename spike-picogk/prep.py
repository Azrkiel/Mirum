import sys,numpy as np,json,fast_simplification as fs
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components as cc
src,out=sys.argv[1:3]; target=int(sys.argv[3])
d=np.memmap(src,dtype=np.uint8,mode='r'); n=int(np.frombuffer(d[80:84],np.uint32)[0])
rec=np.dtype([('n','<3f4'),('v','<9f4'),('a','<u2')]); t=np.frombuffer(d[84:84+50*n],rec)['v'].reshape(-1,3)
u,inv=np.unique(t,axis=0,return_inverse=True); f=inv.reshape(-1,3).astype(np.int64)
bad=(f[:,0]==f[:,1])|(f[:,1]==f[:,2])|(f[:,0]==f[:,2]); deg=int(bad.sum()); f=f[~bad]
def comps(u,f):
    e=np.vstack([f[:,[0,1]],f[:,[1,2]],f[:,[2,0]]]); K=len(u)
    nc,lab=cc(coo_matrix((np.ones(len(e),bool),(e[:,0],e[:,1])),shape=(K,K)),directed=False)
    tl=lab[f[:,0]]; ids,cnt=np.unique(tl,return_counts=True); return tl,ids,cnt,e
tl,ids,cnt,e=comps(u,f); main=ids[np.argmax(cnt)]; f=f[tl==main]
s=np.sort(np.vstack([f[:,[0,1]],f[:,[1,2]],f[:,[2,0]]]),1); uc=np.unique(s[:,0]*len(u)+s[:,1],return_counts=True)[1]
stats=dict(raw_tris=n,degenerate=deg,components=len(ids),cleaned_tris=len(f),edge_uses={int(a):int(b) for a,b in zip(*np.unique(uc,return_counts=True))})
v2,f2=fs.simplify(u.astype(np.float32),f.astype(np.int32),target_count=target)
stats.update(view_tris=len(f2),view_verts=len(v2),bbox=[v2.min(0).round(2).tolist(),v2.max(0).round(2).tolist()])
open(out+'.pos.bin','wb').write(v2.astype('<f4').tobytes()); open(out+'.idx.bin','wb').write(f2.astype('<u4').tobytes())
json.dump(stats,open(out+'.json','w')); print(stats)
