import sys,numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components as cc
d=np.memmap(sys.argv[1],dtype=np.uint8,mode='r'); n=int(np.frombuffer(d[80:84],np.uint32)[0])
rec=np.dtype([('n','<3f4'),('v','<9f4'),('a','<u2')]); t=np.frombuffer(d[84:84+50*n],rec)['v'].reshape(-1,3).astype(np.float64)
u,inv=np.unique(t,axis=0,return_inverse=True); f=inv.reshape(-1,3).astype(np.int64); K=len(u)
bad=(f[:,0]==f[:,1])|(f[:,1]==f[:,2])|(f[:,0]==f[:,2]); print('degenerate',int(bad.sum()),'of',n); f=f[~bad]
e=np.vstack([f[:,[0,1]],f[:,[1,2]],f[:,[2,0]]]); s=np.sort(e,1); ku=s[:,0]*K+s[:,1]
uk,c=np.unique(ku,return_counts=True); print('edge_use_counts',{int(a):int(b) for a,b in zip(*np.unique(c,return_counts=True))})
kd=e[:,0]*K+e[:,1]; print('inconsistent_winding_directed_dupes',int(len(kd)-len(np.unique(kd))))
nc,lab=cc(coo_matrix((np.ones(len(e),bool),(e[:,0],e[:,1])),shape=(K,K)),directed=False)
tl=lab[f[:,0]]; ids,cnt=np.unique(tl,return_counts=True); o=np.argsort(-cnt); print('components',len(ids))
p=u[f]; vol=np.einsum('ij,ij->i',p[:,0],np.cross(p[:,1],p[:,2]))/6
for i in o[:6]:
    m=tl==ids[i]; q=p[m].reshape(-1,3); print(' tris',int(cnt[i]),'signed_vol',round(float(vol[m].sum()),1),'bbox',q.min(0).round(1).tolist(),q.max(0).round(1).tolist())
print('total_signed_vol',round(float(vol.sum()),1))
