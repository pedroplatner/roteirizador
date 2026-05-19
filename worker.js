// Route computation Web Worker
// Roda off-thread: nearestNeighbor, twoOpt, kmeans

function hav(a,b,c,d){
  a=Number(a);b=Number(b);c=Number(c);d=Number(d);
  if(!isFinite(a)||!isFinite(b)||!isFinite(c)||!isFinite(d))return null;
  const R=6371,dL=(c-a)*Math.PI/180,dG=(d-b)*Math.PI/180,
        x=Math.sin(dL/2)**2+Math.cos(a*Math.PI/180)*Math.cos(c*Math.PI/180)*Math.sin(dG/2)**2;
  return R*2*Math.atan2(Math.sqrt(x),Math.sqrt(1-x));
}

function nearestNeighbor(distMatrix,startIdx=0){
  const n=distMatrix.length;
  const visited=new Set([startIdx]);
  const order=[startIdx];
  while(order.length<n){
    const last=order[order.length-1];
    let best=-1,bestD=Infinity;
    for(let j=0;j<n;j++){
      if(!visited.has(j)&&distMatrix[last][j]<bestD){bestD=distMatrix[last][j];best=j;}
    }
    if(best===-1) break;
    visited.add(best);order.push(best);
  }
  return order;
}

function twoOpt(order,distMatrix,destDist){
  const useOpen=Array.isArray(destDist)&&destDist.length===distMatrix.length;
  let improved=true,best=[...order];
  while(improved){
    improved=false;
    for(let i=0;i<best.length-1;i++){
      for(let j=i+1;j<best.length;j++){
        const nextJ=j+1<best.length?best[j+1]:-1;
        const costAfterJ_before=nextJ===-1?(useOpen?destDist[best[j]]:distMatrix[best[j]][best[0]]):distMatrix[best[j]][nextJ];
        const costAfterI_before=distMatrix[best[i]][best[i+1]];
        const before=costAfterI_before+costAfterJ_before;
        const costAfterJ_after=nextJ===-1?(useOpen?destDist[best[i+1]]:distMatrix[best[i+1]][best[0]]):distMatrix[best[i+1]][nextJ];
        const costAfterI_after=distMatrix[best[i]][best[j]];
        const after=costAfterI_after+costAfterJ_after;
        if(after<before-1e-9){
          const rev=best.slice(i+1,j+1).reverse();
          best=[...best.slice(0,i+1),...rev,...best.slice(j+1)];
          improved=true;
        }
      }
    }
  }
  return best;
}

function kmeans(points,k,maxIter=50){
  if(points.length<=k) return points.map((_,i)=>[i]);
  const indices=[...Array(points.length).keys()];
  const shuffle=a=>{for(let i=a.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[a[i],a[j]]=[a[j],a[i]];}return a;};
  let centroids=shuffle([...indices]).slice(0,k).map(i=>({lat:points[i].lat,lng:points[i].lng}));
  let assignments=new Array(points.length).fill(0);
  for(let iter=0;iter<maxIter;iter++){
    let changed=false;
    for(let i=0;i<points.length;i++){
      let best=0,bestD=Infinity;
      for(let c=0;c<k;c++){
        const d=hav(points[i].lat,points[i].lng,centroids[c].lat,centroids[c].lng);
        if(d<bestD){bestD=d;best=c;}
      }
      if(best!==assignments[i]){assignments[i]=best;changed=true;}
    }
    if(!changed) break;
    for(let c=0;c<k;c++){
      const members=points.filter((_,i)=>assignments[i]===c);
      if(!members.length) continue;
      centroids[c]={lat:members.reduce((s,p)=>s+p.lat,0)/members.length,lng:members.reduce((s,p)=>s+p.lng,0)/members.length};
    }
  }
  const groups=Array.from({length:k},()=>[]);
  assignments.forEach((c,i)=>groups[c].push(i));
  return groups.filter(g=>g.length>0);
}

onmessage=function(e){
  const{id,fn,args}=e.data;
  try{
    let result;
    if(fn==='nearestNeighbor') result=nearestNeighbor(args.distMatrix,args.startIdx);
    else if(fn==='twoOpt')     result=twoOpt(args.order,args.distMatrix,args.destDist);
    else if(fn==='kmeans')     result=kmeans(args.points,args.k,args.maxIter||50);
    else throw new Error('fn desconhecida: '+fn);
    postMessage({id,result});
  }catch(err){
    postMessage({id,error:err.message});
  }
};
