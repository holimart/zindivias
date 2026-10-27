#!/usr/bin/env python3
"""Nested county-held-out screen of a partially pooled D3 slope deviation for within-region SVI Q3."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from bias60.inputs import ProvidedInputs,load_config,official_scored_pairs  # noqa: E402

REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
EXPECTED_N=9379;EXPECTED_D3_N=9299;MIN_SVI_N=9298
D3_SHA="5a254127dd2adcd0f36e080a21b54fe9d9def46aeb93c068b8bf80c03359b36c"
RESIDUAL_SHA="437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
D3_REPORT=ROOT/"reports/d3-housing-intensity-residual-screen.json"
D3_SCRIPT=ROOT/"scripts/screen_d3_housing_intensity_residual.py"
D3_HARNESS_REPORT=ROOT/"reports/ideas-harness.json"
D3_CONFIG=ROOT/"config/bias60-harness-preregistered.json"
RESIDUAL_REPORT=ROOT/"reports/s1-disagreement-orthogonalized.json"
BOOTSTRAP_REPLICATES=2000;BOOTSTRAP_SEED=20261107
MIN_DELTA_R2=.002;MIN_CI_LOWER=0.;MAX_STRATUM_DECLINE=.005;MIN_STRATUM_N=100
LAMBDA_GRID=(0.,1e-6,1e-5,1e-4,1e-3,1e-2,1e-1,1.,10.)
INNER_FOLDS=5


def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1<<20),b""):h.update(block)
    return h.hexdigest()


def mask_sha256(mask:np.ndarray)->str:
    return hashlib.sha256(np.packbits(np.asarray(mask,dtype=np.uint8),bitorder="little").tobytes()).hexdigest()


def vector_sha256(geoids:list[str],regions:list[str],values:np.ndarray,defined:np.ndarray)->str:
    if not(len(geoids)==len(regions)==len(values)==len(defined)):raise ValueError("feature hash arrays misaligned")
    payload="\n".join(f"{g},{r},{int(ok)},{format(float(v),'.17g') if ok else ''}" for g,r,v,ok in zip(geoids,regions,values,defined))
    return hashlib.sha256(payload.encode()).hexdigest()


def log_housing_building_intensity(housing:np.ndarray,buildings:np.ndarray,ruca:np.ndarray)->tuple[np.ndarray,np.ndarray]:
    hu=np.asarray(housing,dtype=np.float64);building=np.asarray(buildings,dtype=np.float64);r=np.asarray(ruca,dtype=np.float64)
    if hu.ndim!=1 or building.shape!=hu.shape or r.shape!=hu.shape:raise ValueError("D3 input vectors must align")
    valid_ruca=np.isfinite(r)&(r>=1.)&(r<=10.)
    defined=np.isfinite(hu)&(hu>0.)&np.isfinite(building)&(building>0.)&valid_ruca
    feature=np.full(hu.shape,np.nan);feature[defined]=np.log(hu[defined]/building[defined])
    if np.any(defined&~np.isfinite(feature)):raise ValueError("D3 defined values must be finite")
    return feature,defined


def assign_svi_quartiles(svi:np.ndarray,regions:np.ndarray)->tuple[np.ndarray,dict[str,list[float]]]:
    values=np.asarray(svi,dtype=np.float64);reg=np.asarray(regions,dtype=object)
    if values.ndim!=1 or reg.shape!=values.shape:raise ValueError("SVI and region vectors must align")
    quartile=np.full(len(values),-1,dtype=np.int8);cuts_by_region={}
    for name in REGIONS:
        ix=np.flatnonzero((reg==name)&np.isfinite(values))
        if len(ix)<4:raise ValueError(f"insufficient finite provided SVI in {name}")
        cuts=np.quantile(values[ix],[.25,.5,.75]);cuts_by_region[name]=[float(x) for x in cuts]
        quartile[ix]=np.searchsorted(cuts,values[ix],side="right").astype(np.int8)+1
    return quartile,cuts_by_region


def _design(regions:np.ndarray,x:np.ndarray,q3:np.ndarray,mean:float,sd:float,*,interaction:bool)->np.ndarray:
    reg=np.asarray(regions,dtype=object);value=np.asarray(x,dtype=np.float64);indicator=np.asarray(q3,dtype=bool)
    if reg.ndim!=1 or value.shape!=reg.shape or indicator.shape!=reg.shape:raise ValueError("design vectors must align")
    z=(value-mean)/sd
    dummies=np.column_stack([(reg==r).astype(np.float64) for r in REGIONS[1:]])
    columns=[np.ones(len(reg)),*dummies.T,z]
    if interaction:columns.append(z*indicator.astype(np.float64))
    return np.column_stack(columns)


def fit_partial_pool(train_y:np.ndarray,train_x:np.ndarray,train_regions:np.ndarray,train_q3:np.ndarray,
                     test_x:np.ndarray,test_regions:np.ndarray,test_q3:np.ndarray,shrinkage:float
                     )->tuple[np.ndarray,dict[str,Any]]:
    """Fit global D3 coefficient plus a ridge-shrunk Q3 slope deviation; training-only scaling."""
    y=np.asarray(train_y,dtype=np.float64);x=np.asarray(train_x,dtype=np.float64);reg=np.asarray(train_regions,dtype=object);q=np.asarray(train_q3,dtype=bool)
    xt=np.asarray(test_x,dtype=np.float64);rt=np.asarray(test_regions,dtype=object);qt=np.asarray(test_q3,dtype=bool)
    if (y.ndim!=1 or x.shape!=y.shape or reg.shape!=y.shape or q.shape!=y.shape or xt.ndim!=1
      or rt.shape!=xt.shape or qt.shape!=xt.shape or len(y)<=6 or len(xt)==0 or not np.isfinite(y).all()
      or not np.isfinite(x).all() or not np.isfinite(xt).all()):raise ValueError("fit vectors must be aligned, finite, and adequately sized")
    if not np.isfinite(shrinkage) or shrinkage<0.:raise ValueError("shrinkage must be finite and nonnegative")
    if set(reg.tolist())!=set(REGIONS):raise ValueError("every training fit must contain all four regions")
    mean=float(x.mean());sd=float(x.std(ddof=0))
    if not np.isfinite(sd) or sd<=0.:raise ValueError("training D3 covariate has zero/nonfinite SD")
    design=_design(reg,x,q,mean,sd,interaction=True);held=_design(rt,xt,qt,mean,sd,interaction=True)
    rank=int(np.linalg.matrix_rank(design))
    if rank!=design.shape[1]:raise ValueError("partial-pooling training design is rank deficient")
    gram=design.T@design;rhs=design.T@y
    penalty=np.zeros(design.shape[1]);penalty[-1]=len(y)*float(shrinkage)
    beta=np.linalg.solve(gram+np.diag(penalty),rhs)
    return held@beta,{"training_n":int(len(y)),"heldout_n":int(len(xt)),"training_feature_mean":mean,
      "training_feature_sd":sd,"shrinkage":float(shrinkage),"penalty_multiplier":float(len(y)*shrinkage),
      "global_D3_coefficient":float(beta[-2]),"Q3_deviation_coefficient":float(beta[-1]),
      "Q3_slope":float(beta[-2]+beta[-1]),"training_design_rank":rank,
      "design_columns":int(design.shape[1]),"region_intercepts":{r:float(beta[0] if r==REGIONS[0] else beta[0]+beta[REGIONS.index(r)]) for r in REGIONS}}


def fit_global_d3(train_y:np.ndarray,train_x:np.ndarray,train_regions:np.ndarray,test_x:np.ndarray,test_regions:np.ndarray
                  )->tuple[np.ndarray,dict[str,Any]]:
    y=np.asarray(train_y,dtype=np.float64);x=np.asarray(train_x,dtype=np.float64);reg=np.asarray(train_regions,dtype=object)
    xt=np.asarray(test_x,dtype=np.float64);rt=np.asarray(test_regions,dtype=object)
    if y.shape!=x.shape or reg.shape!=y.shape or xt.shape!=rt.shape or len(y)<=5 or not np.isfinite(y).all() or not np.isfinite(x).all() or not np.isfinite(xt).all():
        raise ValueError("global-only D3 fit inputs are invalid")
    if set(reg.tolist())!=set(REGIONS):raise ValueError("global D3 fit requires all four training regions")
    mean=float(x.mean());sd=float(x.std(ddof=0))
    if not np.isfinite(sd) or sd<=0.:raise ValueError("training D3 feature scale is zero/nonfinite")
    z=(x-mean)/sd;zt=(xt-mean)/sd
    d=np.column_stack([(reg==r).astype(float) for r in REGIONS[1:]])
    dt=np.column_stack([(rt==r).astype(float) for r in REGIONS[1:]])
    train=np.column_stack((np.ones(len(y)),d,z));test=np.column_stack((np.ones(len(xt)),dt,zt))
    rank=int(np.linalg.matrix_rank(train))
    if rank!=train.shape[1]:raise ValueError("global D3 design rank deficient")
    beta=np.linalg.lstsq(train,y,rcond=None)[0]
    return test@beta,{"training_n":int(len(y)),"heldout_n":int(len(xt)),"training_feature_mean":mean,"training_feature_sd":sd,
      "global_D3_coefficient":float(beta[-1]),"training_design_rank":rank}


def _inner_fold_ids(regions:np.ndarray,counties:np.ndarray,train_mask:np.ndarray)->np.ndarray:
    reg=np.asarray(regions,dtype=object);county=np.asarray(counties,dtype=object);use=np.asarray(train_mask,dtype=bool)
    if reg.shape!=county.shape or use.shape!=reg.shape:raise ValueError("inner assignment vectors must align")
    out=np.full(len(reg),-1,dtype=np.int8)
    for region in REGIONS:
        county_ids=sorted(set(county[use&(reg==region)].tolist()))
        if len(county_ids)<INNER_FOLDS:raise ValueError(f"{region} has fewer than {INNER_FOLDS} inner-training counties")
        assign={c:i%INNER_FOLDS for i,c in enumerate(county_ids)}
        for i in np.flatnonzero(use&(reg==region)):out[i]=assign[county[i]]
    return out


def select_shrinkage_inner(train_y:np.ndarray,train_x:np.ndarray,train_regions:np.ndarray,train_q3:np.ndarray,
                           train_counties:np.ndarray,*,grid:tuple[float,...]=LAMBDA_GRID
                           )->dict[str,Any]:
    """Choose one fixed-grid penalty by grouped inner CV using only outer-training counties."""
    y=np.asarray(train_y,dtype=np.float64);x=np.asarray(train_x,dtype=np.float64);reg=np.asarray(train_regions,dtype=object)
    q=np.asarray(train_q3,dtype=bool);county=np.asarray(train_counties,dtype=object)
    if y.ndim!=1 or any(z.shape!=y.shape for z in (x,reg,q,county)) or not np.isfinite(y).all() or not np.isfinite(x).all():
        raise ValueError("inner tuning arrays must align and be finite")
    if not grid or any(not np.isfinite(v) or v<0 for v in grid) or len(set(grid))!=len(grid):raise ValueError("invalid shrinkage grid")
    fold_ids=_inner_fold_ids(reg,county,np.ones(len(y),dtype=bool));sse={float(v):0. for v in grid};heldout=0;details=[]
    for fold in range(INNER_FOLDS):
        valid=fold_ids==fold;fit=~valid
        if not valid.any() or not fit.any():raise ValueError(f"inner fold {fold} is empty")
        scores={}
        for penalty in grid:
            prediction,meta=fit_partial_pool(y[fit],x[fit],reg[fit],q[fit],x[valid],reg[valid],q[valid],float(penalty))
            error=float(np.sum((y[valid]-prediction)**2));sse[float(penalty)]+=error;scores[str(penalty)]=error
        details.append({"fold":fold,"training_n":int(fit.sum()),"heldout_n":int(valid.sum()),
          "heldout_counties":sorted(set(county[valid].tolist())),"heldout_sse_by_shrinkage":scores})
        heldout+=int(valid.sum())
    mean_sse={str(v):float(sse[float(v)]/heldout) for v in grid}
    # Conservative tie-break: if CV error is equal within numerical tolerance, use stronger shrinkage.
    minimum=min(sse.values());tied=[float(v) for v in grid if sse[float(v)]<=minimum+max(1e-15,abs(minimum)*1e-12)]
    selected=max(tied)
    return {"selected_shrinkage":selected,"selection_rule":"minimum pooled inner county-held-out mean squared error; numerical ties choose the largest penalty",
      "inner_fold_count":INNER_FOLDS,"inner_training_n":int(len(y)),"inner_heldout_n":heldout,
      "candidate_grid":list(map(float,grid)),"inner_mean_squared_error_by_shrinkage":mean_sse,
      "inner_fold_diagnostics":details}


def score_vs_zero(y:np.ndarray,prediction:np.ndarray)->dict[str,float|int]:
    target=np.asarray(y,dtype=np.float64);pred=np.asarray(prediction,dtype=np.float64)
    if target.ndim!=1 or pred.shape!=target.shape or not len(target) or not np.isfinite(target).all() or not np.isfinite(pred).all():
        raise ValueError("paired residual vectors must be aligned and finite")
    baseline=float(np.dot(target,target));sse=float(np.sum((target-pred)**2))
    if baseline<=0.:raise ValueError("frozen S1 residual baseline SSE must be positive")
    return {"n":int(len(target)),"frozen_S1_zero_correction_sse":baseline,"model_sse":sse,"delta_r2":(baseline-sse)/baseline}


def county_block_bootstrap(y:np.ndarray,base_d3:np.ndarray,partial:np.ndarray,regions:np.ndarray,counties:np.ndarray,
                           quartile:np.ndarray,urban:np.ndarray,mask:np.ndarray,*,replicates:int=BOOTSTRAP_REPLICATES,
                           seed:int=BOOTSTRAP_SEED)->dict[str,Any]:
    yy=np.asarray(y,dtype=np.float64);g=np.asarray(regions,dtype=object);c=np.asarray(counties,dtype=object);q=np.asarray(quartile,dtype=np.int8)
    u=np.asarray(urban,dtype=np.float64);p0=np.asarray(base_d3,dtype=np.float64);p1=np.asarray(partial,dtype=np.float64);use=np.asarray(mask,dtype=bool)
    if yy.ndim!=1 or any(v.shape!=yy.shape for v in (g,c,q,u,p0,p1,use)) or np.any(use&(~np.isfinite(yy)|~np.isfinite(p0)|~np.isfinite(p1))):raise ValueError("bootstrap arrays misaligned or nonfinite")
    if replicates<100:raise ValueError("at least 100 bootstrap replicates required")
    blocks={}
    for r in REGIONS:
        ids=sorted(set(c[use&(g==r)].tolist()));blocks[r]={cid:np.flatnonzero(use&(g==r)&(c==cid)) for cid in ids}
        if len(ids)<2:raise ValueError(f"insufficient county blocks for {r}")
    strata={"svi_q1":use&(q==1),"svi_q2":use&(q==2),"svi_q3":use&(q==3),"svi_q4":use&(q==4),
      "rural":use&np.isfinite(u)&(u<.5),"urban":use&np.isfinite(u)&(u>=.5)}
    names=("pooled",*REGIONS,*strata.keys());dist={name:np.full(replicates,np.nan) for name in names};d3dist={name:np.full(replicates,np.nan) for name in names}
    rng=np.random.default_rng(seed);invalid=0
    def delta(ix:np.ndarray,aug:bool)->float:
        if len(ix)==0:return float("nan")
        yyb=yy[ix];base=float(np.dot(yyb,yyb))
        if base<=0.:return float("nan")
        pred=p1[ix] if aug else p0[ix]
        return (base-float(np.sum((yyb-pred)**2)))/base
    for b in range(replicates):
        draw_by_region={}
        for r in REGIONS:
            ids=list(blocks[r]);draw=rng.choice(ids,size=len(ids),replace=True)
            draw_by_region[r]=np.concatenate([blocks[r][cid] for cid in draw])
        ix=np.concatenate([draw_by_region[r] for r in REGIONS])
        dist["pooled"][b]=delta(ix,True);d3dist["pooled"][b]=delta(ix,False)
        for r in REGIONS:
            i=draw_by_region[r];dist[r][b]=delta(i,True);d3dist[r][b]=delta(i,False)
        for name,group in strata.items():
            selected=np.concatenate([draw_by_region[r][group[draw_by_region[r]]] for r in REGIONS])
            dist[name][b]=delta(selected,True);d3dist[name][b]=delta(selected,False)
        if not np.isfinite(dist["pooled"][b]):invalid+=1
    def ci(a:np.ndarray)->list[float]:
        good=a[np.isfinite(a)]
        if len(good)<int(.99*replicates):raise ValueError(f"too many degenerate bootstrap draws: {len(good)}/{replicates}")
        return [float(x) for x in np.quantile(good,[.025,.975])]
    return {"replicates":replicates,"seed":seed,"resampling":"Paired county-block bootstrap of outer OOF predictions; sample counties with replacement within each region and retain their tracts.",
      "delta_r2_ci_95":{"partial_pooling_vs_zero_correction":{"pooled":ci(dist["pooled"]),"regional":{r:ci(dist[r]) for r in REGIONS},
        "svi_quartile_and_urbanity_strata":{k:ci(dist[k]) for k in strata if int(strata[k].sum())>=MIN_STRATUM_N}},
        "partial_pooling_increment_vs_global_D3":{"pooled":ci(dist["pooled"]-d3dist["pooled"]),
          "regional":{r:ci(dist[r]-d3dist[r]) for r in REGIONS}}},"degenerate_pooled_replicates":invalid}


def _load_inputs(workspace:Path,official:list[tuple[str,str]],config:dict[str,Any])->tuple[np.ndarray,np.ndarray,np.ndarray,np.ndarray,np.ndarray,dict[str,Any]]:
    n=len(official);pos={p:i for i,p in enumerate(official)}
    hu=np.full(n,np.nan);buildings=np.full(n,np.nan);ruca=np.full(n,np.nan);svi=np.full(n,np.nan);urban=np.full(n,np.nan);access={};files={}
    for region in REGIONS:
        local=[i for i,(_,r) in enumerate(official) if r==region];ids=[official[i][0] for i in local]
        inp=ProvidedInputs(config,workspace,region,ids)
        hv=np.asarray(inp.acs_housing_units(),dtype=np.float64)
        bv=np.asarray(inp.features([config["seeds"]["D3"]["building_count_column"]])[config["seeds"]["D3"]["building_count_column"]],dtype=np.float64)
        strata=inp.strata([config["seeds"]["D3"]["ruca_column"],"svi_minority","pct_urban"])
        ru=np.asarray(strata[config["seeds"]["D3"]["ruca_column"]],dtype=np.float64)
        sv=np.asarray(strata["svi_minority"],dtype=np.float64);urb=np.asarray(strata["pct_urban"],dtype=np.float64)
        hu[local]=hv;buildings[local]=bv;ruca[local]=ru;svi[local]=sv;urban[local]=urb
        access[region]={"accessed":inp.accessed,"provided_acs_object":inp.polygon_layer_path(config["inputs"]["polygon_layer"])}
        fp=workspace/config["inputs"]["feature_cache_template"].format(region=region);sp=workspace/config["inputs"]["strata_cache_template"].format(region=region)
        files[str(fp)]={"sha256":sha256_file(fp),"columns_read":["GEOID","overture_buildings"]}
        files[str(sp)]={"sha256":sha256_file(sp),"columns_read":["GEOID","ruca_primary","svi_minority","pct_urban"]}
    x,defined=log_housing_building_intensity(hu,buildings,ruca)
    if int(defined.sum())!=EXPECTED_D3_N:raise ValueError(f"D3 raw feature support changed: {int(defined.sum())}")
    artifact=workspace/"ideas/D3.parquet"
    if sha256_file(artifact)!=D3_SHA:raise ValueError("frozen D3 emission artifact SHA mismatch")
    table=pq.read_table(artifact,columns=["GEOID","region","defined"])
    if table.num_rows!=n:raise ValueError("D3 artifact row count mismatch")
    artifact_mask=np.zeros(n,dtype=bool);seen=set()
    for row in table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in pos or pair in seen:raise ValueError(f"D3 artifact ID mismatch: {pair}")
        seen.add(pair);artifact_mask[pos[pair]]=bool(row["defined"])
    if seen!=set(official) or not np.array_equal(artifact_mask,defined):raise ValueError("raw D3 feature support differs from frozen D3 emission")
    return x,defined,svi,urban,hu,{"D3_artifact":{"path":str(artifact),"sha256":D3_SHA,"defined_ids":int(defined.sum())},
      "D3_feature_values_and_mask_sha256":vector_sha256([g for g,_ in official],[r for _,r in official],x,defined),
      "provided_input_values_sha256":{"ACS_housing_units":vector_sha256([g for g,_ in official],[r for _,r in official],hu,np.isfinite(hu)),
        "Overture_building_count":vector_sha256([g for g,_ in official],[r for _,r in official],buildings,np.isfinite(buildings)),
        "provided_RUCA":vector_sha256([g for g,_ in official],[r for _,r in official],ruca,np.isfinite(ruca)),
        "provided_SVI_minority":vector_sha256([g for g,_ in official],[r for _,r in official],svi,np.isfinite(svi)),
        "provided_pct_urban":vector_sha256([g for g,_ in official],[r for _,r in official],urban,np.isfinite(urban))},
      "provided_inputs_access":access,"provided_cache_files":files}


def _load_residual(workspace:Path,official:list[tuple[str,str]])->tuple[np.ndarray,dict[str,Any]]:
    path=workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    if sha256_file(path)!=RESIDUAL_SHA:raise ValueError("frozen S1 residual artifact SHA mismatch")
    table=pq.read_table(path,columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"]);n=len(official)
    if table.num_rows!=n:raise ValueError("frozen residual artifact row count mismatch")
    pos={p:i for i,p in enumerate(official)};seen=set();y=np.full(n,np.nan)
    for row in table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in pos or pair in seen:raise ValueError(f"duplicate or unexpected residual GEOID {pair}")
        seen.add(pair);i=pos[pair]
        if row["crossfit_joint_defined"] is not True or row["crossfit_residual"] is None:raise ValueError("frozen S1 residual must cover all official IDs")
        y[i]=float(row["crossfit_residual"])
    if seen!=set(official) or not np.isfinite(y).all():raise ValueError("frozen S1 residual ID alignment failed")
    return y,{"path":str(path),"sha256":RESIDUAL_SHA,"report_path":str(RESIDUAL_REPORT),"report_sha256":sha256_file(RESIDUAL_REPORT)}


def _summary(y:np.ndarray,pred:np.ndarray,mask:np.ndarray)->dict[str,Any]:
    yy=np.asarray(y,dtype=np.float64)[mask];pp=np.asarray(pred,dtype=np.float64)[mask]
    if not len(yy):return {"n":0,"delta_r2":None,"mean_residual":None}
    metric=score_vs_zero(yy,pp)
    return {"n":len(yy),"delta_r2":metric["delta_r2"],"mean_residual":float(yy.mean()),
      "mean_absolute_residual":float(np.mean(np.abs(yy))),"baseline_sse":metric["frozen_S1_zero_correction_sse"],
      "model_sse":metric["model_sse"]}


def run(workspace:Path,report_path:Path)->dict[str,Any]:
    before=json.loads(D3_REPORT.read_text(encoding="utf-8"))
    if (before["measure_after"]["pooled"]["delta_r2"]!=0.0018955696810002054
      or before["measure_after"]["county_block_bootstrap"]["pooled_delta_r2_ci_95"]!=[-0.00977202476871474,0.008578102516979595]
      or before["measure_after"]["equity_stratum_residual_summaries_and_deltas"]["svi_q3"]["heldout_delta_r2"]!=-0.006282115795467945):
        raise ValueError("frozen D3 MEASURE-BEFORE differs from assigned source screen")
    config=load_config(D3_CONFIG);harness=json.loads(D3_HARNESS_REPORT.read_text(encoding="utf-8"))["per_idea"]["D3"]
    if harness["sha256"]!=D3_SHA or harness["emissions"][0]["defined_rows"]!=EXPECTED_D3_N:raise ValueError("frozen D3 artifact/support changed")
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or len({g for g,_ in official})!=EXPECTED_N or set(r for _,r in official)!=set(REGIONS):raise ValueError("official ID partition invalid")
    geoids=np.asarray([g for g,_ in official],dtype=object);regions=np.asarray([r for _,r in official],dtype=object);counties=np.asarray([g[:5] for g in geoids],dtype=object)
    residual,residual_meta=_load_residual(workspace,official);x,d3_defined,svi,urban,_housing_units,input_meta=_load_inputs(workspace,official,config)
    quartile,cuts=assign_svi_quartiles(svi,regions)
    if cuts!=before["measure_after"]["within_region_svi_quartile_cutpoints"]:raise ValueError("SVI quartile boundaries differ from frozen D3 screen")
    support=d3_defined&np.isfinite(residual)&(quartile>0)
    if int(d3_defined.sum())!=EXPECTED_D3_N or int(support.sum())!=MIN_SVI_N:raise ValueError(f"unexpected paired D3/SVI support: {int(support.sum())}")
    q3=(quartile==3);base_oof=np.full(EXPECTED_N,np.nan);partial_oof=np.full(EXPECTED_N,np.nan);selected=[];outer_rows=[]
    county_ids_by_region={r:sorted(set(counties[support&(regions==r)].tolist())) for r in REGIONS}
    for region in REGIONS:
        regional=support&(regions==region)
        for county in county_ids_by_region[region]:
            test=regional&(counties==county);train=support&~test
            tuning=select_shrinkage_inner(residual[train],x[train],regions[train],q3[train],counties[train])
            beta_pred,base_meta=fit_global_d3(residual[train],x[train],regions[train],x[test],regions[test])
            aug_pred,aug_meta=fit_partial_pool(residual[train],x[train],regions[train],q3[train],x[test],regions[test],q3[test],tuning["selected_shrinkage"])
            base_oof[test]=beta_pred;partial_oof[test]=aug_pred;selected.append(tuning["selected_shrinkage"])
            outer_rows.append({"region":region,"heldout_county_fips":str(county),"heldout_n":int(test.sum()),"training_n":int(train.sum()),
              "selected_shrinkage":tuning["selected_shrinkage"],"inner_mean_squared_error_by_shrinkage":tuning["inner_mean_squared_error_by_shrinkage"],
              "inner_fold_count":tuning["inner_fold_count"],"inner_training_n":tuning["inner_training_n"],"inner_heldout_n":tuning["inner_heldout_n"],
              "inner_selection_rule":tuning["selection_rule"],"outer_training_feature_mean":aug_meta["training_feature_mean"],
              "outer_training_feature_sd":aug_meta["training_feature_sd"],"outer_global_D3_coefficient":aug_meta["global_D3_coefficient"],
              "outer_Q3_deviation_coefficient":aug_meta["Q3_deviation_coefficient"],"outer_Q3_slope":aug_meta["Q3_slope"],
              "outer_design_rank":aug_meta["training_design_rank"],"baseline_design_rank":base_meta["training_design_rank"]})
    if np.any(support&(~np.isfinite(base_oof)|~np.isfinite(partial_oof))) or np.any(~support&(np.isfinite(base_oof)|np.isfinite(partial_oof))):raise AssertionError("OOF predictions must exactly match paired support")
    base_metric=score_vs_zero(residual[support],base_oof[support]);partial_metric=score_vs_zero(residual[support],partial_oof[support])
    pooled_increment=(float(np.dot(residual[support],residual[support])-np.sum((residual[support]-partial_oof[support])**2))) / float(np.dot(residual[support],residual[support]))
    overall_comparison={"global_D3_only_delta_r2_vs_zero":base_metric["delta_r2"],"partial_pooling_delta_r2_vs_zero":partial_metric["delta_r2"],
      "partial_pooling_incremental_delta_r2_vs_global_D3":(float(np.sum((residual[support]-base_oof[support])**2))-float(np.sum((residual[support]-partial_oof[support])**2)))/float(np.dot(residual[support],residual[support]))}
    strata_masks={f"svi_q{q}":support&(quartile==q) for q in range(1,5)}
    strata_masks.update({"rural":support&np.isfinite(urban)&(urban<.5),"urban":support&np.isfinite(urban)&(urban>=.5)})
    regional={};strata={};
    for r in REGIONS:
        mask=support&(regions==r);regional[r]={"official_ids":int(np.sum(regions==r)),"D3_defined_ids":int(np.sum(d3_defined&(regions==r))),
          "joint_D3_SVI_support":int(mask.sum()),"heldout_counties":len(county_ids_by_region[r]),
          "global_D3_only":score_vs_zero(residual[mask],base_oof[mask]),"partial_pooling":score_vs_zero(residual[mask],partial_oof[mask]),
          "partial_pooling_increment_vs_global_D3":(float(np.sum((residual[mask]-base_oof[mask])**2))-float(np.sum((residual[mask]-partial_oof[mask])**2)))/float(np.dot(residual[mask],residual[mask]))}
    for name,mask in strata_masks.items():
        n=int(mask.sum()); sufficient=n>=MIN_STRATUM_N
        strata[name]={"support_ids":n,"sufficient_support_for_gate":sufficient,
          "global_D3_only":score_vs_zero(residual[mask],base_oof[mask]) if n>1 else None,
          "partial_pooling":score_vs_zero(residual[mask],partial_oof[mask]) if n>1 else None,
          "partial_pooling_increment_vs_global_D3":((float(np.sum((residual[mask]-base_oof[mask])**2))-float(np.sum((residual[mask]-partial_oof[mask])**2)))/float(np.dot(residual[mask],residual[mask]))) if n>1 and np.dot(residual[mask],residual[mask])>0. else None,
          "mean_frozen_residual":float(residual[mask].mean()) if n else None}
    boot=county_block_bootstrap(residual,base_oof,partial_oof,regions,counties,quartile,urban,support)
    pooled_ci=boot["delta_r2_ci_95"]["partial_pooling_vs_zero_correction"]["pooled"]
    q3delta=strata["svi_q3"]["partial_pooling"]["delta_r2"]
    other_strata={k:v["partial_pooling"]["delta_r2"] for k,v in strata.items() if k!="svi_q3" and v["sufficient_support_for_gate"] and v["partial_pooling"] is not None}
    worst_other=min(other_strata.values()) if other_strata else None
    passed=bool(partial_metric["delta_r2"]>=MIN_DELTA_R2 and pooled_ci[0]>MIN_CI_LOWER and q3delta>=-MAX_STRATUM_DECLINE
      and worst_other is not None and worst_other>=-MAX_STRATUM_DECLINE)
    report={"schema_version":1,"screen":"Nested county-held-out D3 global slope with one SVI-Q3 deviation shrunk toward the global coefficient; diagnostic-only",
      "measure_before":{"D3_defined_ids":EXPECTED_D3_N,"official_ids":EXPECTED_N,"pooled_D3_global_delta_r2_vs_zero":before["measure_after"]["pooled"]["delta_r2"],
        "pooled_D3_county_block_ci_95":before["measure_after"]["county_block_bootstrap"]["pooled_delta_r2_ci_95"],
        "pooled_D3_SVI_Q3_delta_r2":before["measure_after"]["equity_stratum_residual_summaries_and_deltas"]["svi_q3"]["heldout_delta_r2"],
        "prior_D3_report_sha256":sha256_file(D3_REPORT)},
      "method":{"outcome":"Frozen cross-fitted S1 Queen/Rook disagreement residual; no refit and no labels.",
        "D3_covariate":"Provided log housing-unit-to-Overture-building-count intensity, exactly masked to frozen D3 support. ACS housing units are a covariate only; not a structure denominator, floor, reference, or target proxy.",
        "model":"Region fixed intercepts plus one global standardized D3 slope and a single SVI-Q3 slope deviation. Penalize only the Q3 deviation by lambda * n_train * deviation^2; lambda=0 is unpooled and large lambda approaches a common slope.",
        "quartiles":"Within-region empirical quartiles of provided svi_minority using the frozen prior D3 cutpoints and right-sided tie assignment; no residual values used in binning.",
        "outer_validation":"Leave one supported five-digit GEOID county out within its region; produce paired D3-only and partially pooled predictions on the same support.",
        "inner_tuning":"Select one shrinkage value from the fixed grid by pooled five-fold county-grouped CV on each outer training set only. County IDs are sorted within region then assigned round-robin to five inner folds; all scaling and coefficients are fit on inner-training tracts only. Numerical MSE ties choose stronger shrinkage.",
        "scaling":"Standardize D3 using training-fold mean and population SD; use that scale for the Q3 interaction. Outer held-out counties do not contribute to fold-specific transforms, coefficients, or lambda selection.",
        "metric":"Primary held-out ΔR²=(zero-correction frozen-S1 SSE - model OOF SSE)/zero-correction SSE. Also report the partial-pooling SSE gain relative to the global-D3-only OOF model.",
        "uncertainty":"Paired county-block bootstrap of outer OOF predictions, sampling counties with replacement within each region; report pooled and regional CIs and supported stratum CIs.",
        "acceptance":"Pooled partial-pooling ΔR² >=0.002; pooled bootstrap lower bound >0; Q3 ΔR² >=-0.005; every other supported SVI quartile and rural/urban stratum ΔR² >=-0.005.",
        "no_target_tuning":True,"no_estimator_promotion":True,"interpretation_limit":"Provided-feature residual screen only; not target accuracy or causality."},
      "scope":{"official_ids":EXPECTED_N,"official_geoid_region_sha256":hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest(),
        "D3_emitted_defined_ids":int(d3_defined.sum()),"D3_SVI_joint_heldout_support":int(support.sum()),"joint_undefined_ids":int((~support).sum()),
        "D3_mask_sha256":mask_sha256(d3_defined),"joint_mask_sha256":mask_sha256(support),
        "D3_SVI_values_and_mask_sha256":hashlib.sha256("\n".join(f"{g},{r},{format(float(xi),'.17g') if ok else ''},{int(ok)},{format(float(si),'.17g') if np.isfinite(si) else ''},{int(qi)}" for g,r,xi,ok,si,qi in zip(geoids,regions,x,d3_defined,svi,quartile)).encode()).hexdigest(),
        "feature_support_by_region":{r:{"official_ids":int(np.sum(regions==r)),"D3_defined":int(np.sum(d3_defined&(regions==r))),
          "joint_D3_SVI":int(np.sum(support&(regions==r))),"supported_counties":len(county_ids_by_region[r])} for r in REGIONS},
        "heldout_counties":len(outer_rows),"labels_read":False,"sample_score_values_read":False,"target_reconstruction":False,
        "external_inputs_used":False,"removed_or_equivalent_references_read":False,"ACS_used_as_structure_denominator_or_floor":False,
        "leaderboard_feedback_used":False,"uploads":0,"estimator_or_registry_changed":False},
      "measure_after":{"global_D3_only_vs_zero":base_metric,"partial_pooling_vs_zero":partial_metric,"pooled_comparison":overall_comparison,
        "pooled_county_block_bootstrap_ci_95":pooled_ci,"partial_pooling_increment_vs_global_D3_ci_95":boot["delta_r2_ci_95"]["partial_pooling_increment_vs_global_D3"]["pooled"],
        "regional":regional,"county_block_bootstrap":boot,"stratum_deltas_and_support":strata,
        "within_region_svi_quartile_cutpoints":cuts,"selected_shrinkage_distribution":{str(v):int(selected.count(v)) for v in LAMBDA_GRID},
        "outer_county_folds":outer_rows},
      "thresholds":{"pooled_delta_r2_minimum_inclusive":MIN_DELTA_R2,"pooled_ci_lower_strictly_above":MIN_CI_LOWER,
        "maximum_Q3_decline":MAX_STRATUM_DECLINE,"maximum_other_supported_stratum_decline":MAX_STRATUM_DECLINE,
        "minimum_supported_stratum_n":MIN_STRATUM_N,"shrinkage_grid":list(LAMBDA_GRID),"inner_county_folds":INNER_FOLDS},
      "acceptance":{"pooled_delta_r2_at_least_0_002":bool(partial_metric["delta_r2"]>=MIN_DELTA_R2),
        "pooled_county_ci_lower_above_zero":bool(pooled_ci[0]>MIN_CI_LOWER),
        "Q3_decline_not_over_0_005":bool(q3delta>=-MAX_STRATUM_DECLINE),
        "no_other_supported_stratum_decline_over_0_005":bool(worst_other is not None and worst_other>=-MAX_STRATUM_DECLINE),
        "worst_other_supported_stratum_delta_r2":worst_other,"screen_pass":passed},
      "provenance":{"D3_prior_report":{"path":str(D3_REPORT),"sha256":sha256_file(D3_REPORT)},
        "D3_prior_script":{"path":str(D3_SCRIPT),"sha256":sha256_file(D3_SCRIPT)},"D3_harness_report":{"path":str(D3_HARNESS_REPORT),"sha256":sha256_file(D3_HARNESS_REPORT)},
        "D3_artifact":input_meta["D3_artifact"],"frozen_S1_residual":{"path":residual_meta["path"],"sha256":residual_meta["sha256"],"report_sha256":residual_meta["report_sha256"]},
        "provided_input_values_sha256":input_meta["provided_input_values_sha256"],"provided_cache_files":input_meta["provided_cache_files"],
        "provided_inputs_access":input_meta["provided_inputs_access"],"analysis_script_sha256":sha256_file(Path(__file__).resolve()),
        "bootstrap_seed":BOOTSTRAP_SEED,"bootstrap_replicates":BOOTSTRAP_REPLICATES,"candidate_shrinkage_grid":list(LAMBDA_GRID)},
      "interpretation":"The nested partially pooled D3 screen failed one or more preregistered gates; retain diagnostic-only and do not integrate." if not passed else
        "The nested partially pooled D3 screen passed all preregistered diagnostic thresholds; still requires separate authorization before any estimator use."}
    report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""));parser.add_argument("--out",default="reports/d3-svi-q3-partial-pooling.json")
    args=parser.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():parser.error("COMPETITION_WORKSPACE must be an existing directory")
    r=run(workspace,ROOT/args.out)
    print(json.dumps({"scope":r["scope"],"pooled_global_D3":r["measure_after"]["global_D3_only_vs_zero"],
      "pooled_partial_pooling":r["measure_after"]["partial_pooling_vs_zero"],"pooled_ci":r["measure_after"]["pooled_county_block_bootstrap_ci_95"],
      "regional":r["measure_after"]["regional"],"strata":r["measure_after"]["stratum_deltas_and_support"],"acceptance":r["acceptance"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
