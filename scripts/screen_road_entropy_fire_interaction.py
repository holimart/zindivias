#!/usr/bin/env python3
"""Five-fold held-out screen of named-road entropy × provided post-2018 burn-share vs frozen S1 residual."""
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
sys.path.insert(0,str(ROOT/"src"));sys.path.insert(0,str(ROOT/"scripts"))
from bias60.inputs import official_scored_pairs  # noqa: E402
from audit_mx4p_conditional_innovation import read_emission  # noqa: E402

REGIONS=("maricopa-az","northern-ca","eastern-ok","south-central-tx")
NON_FIRE_REGIONS=("maricopa-az","eastern-ok","south-central-tx")
EXPECTED_N=9379
N_FOLDS=5
BOOTSTRAP_REPLICATES=2000
BOOTSTRAP_SEED=20261104
FIRE_THRESHOLD_YEAR=2018
MIN_DELTA_R2=.005
MIN_CI_LOWER=0.
MAX_NONFIRE_DECLINE=-.005
ENTROPY_SHA="41610caca547f76604a8430f7cc0dfcdf628ea3f431632c5e5da11809da92f38"
RESIDUAL_SHA="437b8995e9aa97cc3cf0337cf4338c34e21ade2edaa6dc258700956695feab1e"
MX4_SHA="8d556bab39b45da8d232e3070933e5272096cc611f4286a79e3eae7755ebb9af"
S1_SHA="632829cf29281aa16487707cb555f52b4579cb0edfc0dd3faae360fa8f1dda03"
TM2_SHA="470c23b2902cbb652dfc884c22ba2a743a85538aa819ddd179b1ff2a3aea41bc"
ENTROPY_REPORT=ROOT/"reports/named-road-class-entropy.json"
TM2_FAMILY_REPORT=ROOT/"reports/bias60-family-tm-summary.json"
TM2_FAMILY_SHA="71546cda0eae449c3648a36fa37cd04860815dadffffb9cd6261c2a77364e4b8"
RESIDUAL_REPORT=ROOT/"reports/s1-disagreement-orthogonalized.json"


def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1<<20),b""):h.update(block)
    return h.hexdigest()


def mask_sha256(mask:np.ndarray)->str:
    return hashlib.sha256(np.packbits(np.asarray(mask,dtype=np.uint8),bitorder="little").tobytes()).hexdigest()


def array_hash(ids:list[str],regions:np.ndarray,arrays:list[np.ndarray],mask:np.ndarray)->str:
    if len(ids)!=len(regions) or np.asarray(mask).shape!=(len(ids),) or any(np.asarray(a).shape!=(len(ids),) for a in arrays):
        raise ValueError("array hash inputs are not aligned")
    lines=[]
    for i,(geoid,region) in enumerate(zip(ids,regions)):
        cells=[geoid,str(region),str(int(mask[i]))]
        cells.extend(format(float(a[i]),".17g") if np.isfinite(a[i]) else "" for a in arrays)
        lines.append(",".join(cells))
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _fit_scaler(train:np.ndarray)->tuple[float,float]:
    x=np.asarray(train,dtype=np.float64)
    if x.ndim!=1 or not len(x) or not np.isfinite(x).all():raise ValueError("scaler training vector must be nonempty and finite")
    mean=float(x.mean());sd=float(x.std(ddof=0))
    if not np.isfinite(sd) or sd<=0.:raise ValueError("training-only feature scale is zero/nonfinite")
    return mean,sd


def crossfit_interaction(ids:list[str],regions:np.ndarray,folds:np.ndarray,outcome:np.ndarray,
                         baseline:np.ndarray,road_entropy:np.ndarray,burn_share:np.ndarray,
                         mask:np.ndarray)->dict[str,Any]:
    """Fixed region-intercept baseline plus one train-scaled road-entropy × fire-share term."""
    reg=np.asarray(regions,dtype=object);fold=np.asarray(folds,dtype=np.int8);y=np.asarray(outcome,dtype=np.float64)
    x=np.asarray(baseline,dtype=np.float64);entropy=np.asarray(road_entropy,dtype=np.float64)
    fire=np.asarray(burn_share,dtype=np.float64);use=np.asarray(mask,dtype=bool);n=len(ids)
    if (len(set(ids))!=n or reg.shape!=(n,) or fold.shape!=(n,) or y.shape!=(n,) or x.shape!=(n,4)
        or entropy.shape!=(n,) or fire.shape!=(n,) or use.shape!=(n,)):
        raise ValueError("crossfit arrays must align to unique official GEOIDs")
    if set(reg.tolist())!=set(REGIONS) or np.any((fold<0)|(fold>=N_FOLDS)):
        raise ValueError("require all official regions and fixed five-fold IDs")
    if np.any(use&(~np.isfinite(y)|~np.isfinite(x).all(axis=1)|~np.isfinite(entropy)|~np.isfinite(fire))):
        raise ValueError("complete-case rows must have finite target and all predictors")
    base_oof=np.full(n,np.nan);aug_oof=np.full(n,np.nan);interaction_oof=np.full(n,np.nan)
    fold_details=[];region_codes={r:i for i,r in enumerate(REGIONS)}
    for f in range(N_FOLDS):
        train=use&(fold!=f);test=use&(fold==f)
        if not test.any() or train.sum()<=10:raise ValueError(f"fold {f} has insufficient train/test support")
        train_rows=np.flatnonzero(train);test_rows=np.flatnonzero(test)
        base_train=np.empty((train.sum(),x.shape[1]));base_test=np.empty((test.sum(),x.shape[1]));base_scales={}
        for j,name in enumerate(("log_total_named_road_km","pct_urban","Mx4_F","S1_lagged_Mx4_F")):
            mean,sd=_fit_scaler(x[train,j]);base_train[:,j]=(x[train,j]-mean)/sd;base_test[:,j]=(x[test,j]-mean)/sd
            base_scales[name]={"training_mean":mean,"training_sd":sd}
        emean,esd=_fit_scaler(entropy[train]);fmean,fsd=_fit_scaler(fire[train])
        e_train=(entropy[train]-emean)/esd;e_test=(entropy[test]-emean)/esd
        f_train=(fire[train]-fmean)/fsd;f_test=(fire[test]-fmean)/fsd
        product_train=e_train*f_train;product_test=e_test*f_test
        product_mean,product_sd=_fit_scaler(product_train)
        product_train=(product_train-product_mean)/product_sd;product_test=(product_test-product_mean)/product_sd
        codes_train=np.asarray([region_codes[r] for r in reg[train]],dtype=np.int8)
        codes_test=np.asarray([region_codes[r] for r in reg[test]],dtype=np.int8)
        d_train=np.column_stack([(codes_train==k).astype(float) for k in range(1,len(REGIONS))])
        d_test=np.column_stack([(codes_test==k).astype(float) for k in range(1,len(REGIONS))])
        design_base_train=np.column_stack((np.ones(train.sum()),d_train,base_train))
        design_base_test=np.column_stack((np.ones(test.sum()),d_test,base_test))
        design_aug_train=np.column_stack((design_base_train,product_train))
        design_aug_test=np.column_stack((design_base_test,product_test))
        beta0,_,rank0,sing0=np.linalg.lstsq(design_base_train,y[train],rcond=None)
        beta1,_,rank1,sing1=np.linalg.lstsq(design_aug_train,y[train],rcond=None)
        if rank0!=design_base_train.shape[1] or rank1!=design_aug_train.shape[1]:raise ValueError(f"rank-deficient design in fold {f}")
        base_oof[test]=design_base_test@beta0;aug_oof[test]=design_aug_test@beta1;interaction_oof[test]=product_test
        fold_details.append({"fold":f,"training_n":int(train.sum()),"heldout_n":int(test.sum()),
          "baseline_design_columns":int(design_base_train.shape[1]),"augmented_design_columns":int(design_aug_train.shape[1]),
          "baseline_design_rank":int(rank0),"augmented_design_rank":int(rank1),"interaction_coefficient":float(beta1[-1]),
          "training_entropy_mean":emean,"training_entropy_sd":esd,"training_burn_share_mean":fmean,"training_burn_share_sd":fsd,
          "training_product_mean":product_mean,"training_product_sd":product_sd,"training_only_baseline_scalers":base_scales,
          "baseline_min_singular_value":float(sing0.min()),"augmented_min_singular_value":float(sing1.min())})
    if any(np.any(use&~np.isfinite(z)) or np.any(~use&np.isfinite(z)) for z in (base_oof,aug_oof,interaction_oof)):
        raise AssertionError("out-of-fold predictions must exactly match complete-case support")
    return {"baseline_oof":base_oof,"augmented_oof":aug_oof,"interaction_oof":interaction_oof,"fold_details":fold_details}


def delta_r2(y:np.ndarray,base:np.ndarray,aug:np.ndarray)->dict[str,float]:
    target=np.asarray(y,dtype=np.float64);p0=np.asarray(base,dtype=np.float64);p1=np.asarray(aug,dtype=np.float64)
    if target.ndim!=1 or p0.shape!=target.shape or p1.shape!=target.shape or len(target)<2:
        raise ValueError("paired metrics need aligned vectors of at least two rows")
    if not(np.isfinite(target).all() and np.isfinite(p0).all() and np.isfinite(p1).all()):raise ValueError("metric inputs must be finite")
    sst=float(np.sum((target-target.mean())**2));sse0=float(np.sum((target-p0)**2));sse1=float(np.sum((target-p1)**2))
    if sst<=0.:raise ValueError("held-out residual variance must be positive")
    return {"n":int(len(target)),"baseline_sse":sse0,"augmented_sse":sse1,"heldout_sst":sst,
      "baseline_r2":1.-sse0/sst,"augmented_r2":1.-sse1/sst,"delta_r2":(sse0-sse1)/sst}


def bootstrap_deltas(region_data:dict[str,tuple[np.ndarray,np.ndarray,np.ndarray]],*,replicates:int=BOOTSTRAP_REPLICATES,
                     seed:int=BOOTSTRAP_SEED)->dict[str,Any]:
    if replicates<100 or set(region_data)!=set(REGIONS):raise ValueError("need >=100 replicates and all four regions")
    rng=np.random.default_rng(seed);pooled=np.empty(replicates);regional={r:np.empty(replicates) for r in REGIONS}
    for b in range(replicates):
        yy=[];bb=[];aa=[]
        for region in REGIONS:
            y,p0,p1=(np.asarray(v,dtype=np.float64) for v in region_data[region])
            if y.ndim!=1 or p0.shape!=y.shape or p1.shape!=y.shape or len(y)<2 or not all(np.isfinite(v).all() for v in (y,p0,p1)):
                raise ValueError(f"invalid bootstrap vectors for {region}")
            ix=rng.integers(0,len(y),size=len(y));ys=y[ix];bs=p0[ix];ass=p1[ix]
            regional[region][b]=delta_r2(ys,bs,ass)["delta_r2"]
            yy.append(ys);bb.append(bs);aa.append(ass)
        pooled[b]=delta_r2(np.concatenate(yy),np.concatenate(bb),np.concatenate(aa))["delta_r2"]
    def ci(a:np.ndarray)->list[float]:return [float(v) for v in np.quantile(a,[.025,.975])]
    return {"replicates":replicates,"seed":seed,"resampling":"paired tract bootstrap within each region; pooled interval stratified by region and preserves each observed region support size",
      "pooled_delta_r2_ci_95":ci(pooled),"regional_delta_r2_ci_95":{r:ci(a) for r,a in regional.items()},
      "degenerate_replicates":0}


def _read_entropy(workspace:Path,official:list[tuple[str,str]])->tuple[dict[str,np.ndarray],dict[str,Any]]:
    report=json.loads(ENTROPY_REPORT.read_text(encoding="utf-8"));meta=report["provenance"]["output_artifact"]
    path=Path(meta["path"])
    if sha256_file(path)!=ENTROPY_SHA or meta["sha256"]!=ENTROPY_SHA:raise ValueError("frozen named-road entropy artifact SHA mismatch")
    table=pq.read_table(path,columns=["GEOID","region","class_mix_entropy","entropy_defined","total_named_road_km","pct_urban","Mx4_F","Mx4_F_defined","S1_lagged_Mx4_F","S1_lagged_Mx4_F_defined","fold"])
    if table.num_rows!=EXPECTED_N:raise ValueError("entropy artifact row count mismatch")
    pos={pair:i for i,pair in enumerate(official)};seen=set();n=len(official)
    vals={k:np.full(n,np.nan) for k in ("entropy","road_km","pct_urban","mx4","s1")}
    masks={k:np.zeros(n,dtype=bool) for k in ("entropy","mx4","s1")};folds=np.full(n,-1,dtype=np.int8)
    for row in table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in pos or pair in seen:raise ValueError(f"unexpected/duplicate official entropy ID: {pair}")
        seen.add(pair);i=pos[pair]
        for key,col in (("entropy","class_mix_entropy"),("road_km","total_named_road_km"),("pct_urban","pct_urban"),("mx4","Mx4_F"),("s1","S1_lagged_Mx4_F")):
            if row[col] is not None:vals[key][i]=float(row[col])
        for key,col in (("entropy","entropy_defined"),("mx4","Mx4_F_defined"),("s1","S1_lagged_Mx4_F_defined")):
            masks[key][i]=bool(row[col])
        folds[i]=int(row["fold"])
    if seen!=set(official):raise ValueError("entropy artifact official ID alignment incomplete")
    for key in vals:
        mask=masks[key] if key in masks else np.isfinite(vals[key])
        if not np.array_equal(np.isfinite(vals[key]),mask):raise ValueError(f"entropy artifact {key} mask mismatch")
    if np.any(masks["entropy"]&((vals["entropy"]<0.)|(vals["entropy"]>1.))):raise ValueError("entropy outside [0,1]")
    if np.any(masks["entropy"]&((vals["road_km"]<=0.)|~np.isfinite(vals["road_km"]))):raise ValueError("entropy requires positive named-road length")
    if np.any(np.isfinite(vals["pct_urban"])&((vals["pct_urban"]<0.)|(vals["pct_urban"]>1.))):raise ValueError("pct_urban outside [0,1]")
    vals["fold"]=folds;vals["entropy_defined"]=masks["entropy"];vals["mx4_defined"]=masks["mx4"];vals["s1_defined"]=masks["s1"]
    return vals,{"path":str(path),"sha256":ENTROPY_SHA,"report_sha256":sha256_file(ENTROPY_REPORT),"rows":table.num_rows,
      "entropy_defined":int(masks["entropy"].sum()),"columns_read":table.column_names}


def _read_residual(workspace:Path,official:list[tuple[str,str]])->tuple[np.ndarray,dict[str,Any]]:
    path=workspace/"s1-disagreement-orthogonalized/orthogonalized-disagreement.parquet"
    if sha256_file(path)!=RESIDUAL_SHA:raise ValueError("frozen S1 residual artifact SHA mismatch")
    table=pq.read_table(path,columns=["GEOID","region","crossfit_residual","crossfit_joint_defined"])
    if table.num_rows!=len(official):raise ValueError("S1 residual row count mismatch")
    pos={p:i for i,p in enumerate(official)};seen=set();y=np.full(len(official),np.nan)
    for row in table.to_pylist():
        pair=(str(row["GEOID"]),str(row["region"]))
        if pair not in pos or pair in seen:raise ValueError(f"unexpected/duplicate S1 residual ID {pair}")
        seen.add(pair);i=pos[pair]
        if row["crossfit_joint_defined"] is not True or row["crossfit_residual"] is None:raise ValueError("frozen S1 residual must cover all official tracts")
        y[i]=float(row["crossfit_residual"])
    if seen!=set(official) or not np.isfinite(y).all():raise ValueError("S1 residual support differs from official IDs")
    report=json.loads(RESIDUAL_REPORT.read_text(encoding="utf-8"))
    return y,{"path":str(path),"sha256":RESIDUAL_SHA,"report_sha256":sha256_file(RESIDUAL_REPORT),"report_output_sha256":report["provenance"]["output_artifact"]["sha256"]}


def _read_fire(workspace:Path,official:list[tuple[str,str]])->tuple[np.ndarray,np.ndarray,dict[str,Any]]:
    family=json.loads(TM2_FAMILY_REPORT.read_text(encoding="utf-8"))
    if sha256_file(TM2_FAMILY_REPORT)!=TM2_FAMILY_SHA:raise ValueError("frozen Tm family summary SHA mismatch")
    tm2=family["ideas"]["Tm2"];emission=next(e for e in tm2["emissions"] if e["channel"]=="covariate:postfire_burn_share")
    if tm2["sha256"]!=TM2_SHA or emission["defined_rows"]!=9377:raise ValueError("registered Tm2 postfire covariate provenance changed")
    artifact=workspace/"ideas/Tm2.parquet"
    feature,feature_defined,feature_sha=read_emission(artifact,TM2_SHA,"Tm2","F","covariate:postfire_burn_share",official)
    pos={p:i for i,p in enumerate(official)};burn_share=np.zeros(len(official));defined=np.zeros(len(official),dtype=bool);files={}
    for region in REGIONS:
        path=workspace/"defined-flags-inputs"/f"{region}-strata-tract-table.parquet"
        table=pq.read_table(path,columns=["GEOID","mtbs_covered","mtbs_wildfire_burned_pct_land","mtbs_wildfire_last_year"])
        local={g:i for (g,r),i in pos.items() if r==region};seen=set()
        for row in table.to_pylist():
            geoid=str(row["GEOID"])
            if geoid not in local:continue
            if geoid in seen:raise ValueError(f"duplicate fire strata ID {geoid}")
            seen.add(geoid);i=local[geoid]
            cov=row["mtbs_covered"]
            covered=bool(cov) if isinstance(cov,bool) else (cov is not None and np.isfinite(float(cov)) and float(cov)!=0.)
            burned=np.nan if row["mtbs_wildfire_burned_pct_land"] is None else float(row["mtbs_wildfire_burned_pct_land"])
            last=np.nan if row["mtbs_wildfire_last_year"] is None else float(row["mtbs_wildfire_last_year"])
            valid_burn=np.isfinite(burned);post_fire=np.isfinite(last) and last>=FIRE_THRESHOLD_YEAR
            row_defined=covered and valid_burn and (np.isfinite(last) or burned==0.)
            defined[i]=row_defined
            if row_defined and post_fire:burn_share[i]=float(np.clip(burned/100.,0.,1.))
        if seen!=set(local):raise ValueError(f"provided fire strata do not exactly cover official IDs in {region}")
        files[region]={"path":str(path),"sha256":sha256_file(path),"columns_read":["GEOID","mtbs_covered","mtbs_wildfire_burned_pct_land","mtbs_wildfire_last_year"],
          "fire_field_rows":len(seen),"post2018_burn_positive":int(np.sum((np.asarray([r==region for _,r in official]))&defined&(burn_share>0.)))}
    if not np.array_equal(defined,feature_defined):raise ValueError("raw MTBS-derived support differs from registered Tm2 postfire covariate")
    if not np.allclose(burn_share[defined],feature[defined],rtol=0.,atol=1e-15):raise ValueError("raw provided fire fields differ from registered Tm2 covariate")
    return burn_share,defined,{"registered_Tm2_artifact":{"path":str(artifact),"sha256":feature_sha,"defined_ids":int(feature_defined.sum())},
      "Tm_family_report":{"path":str(TM2_FAMILY_REPORT),"sha256":TM2_FAMILY_SHA},"strata_fire_by_region":files,
      "definition":"Tm2|F|covariate:postfire_burn_share: clip(mtbs_wildfire_burned_pct_land/100,0,1) when mtbs_covered, burn share finite, and last year >=2018; defined no-event rows receive zero under the frozen Tm2 definition."}


def _feature_emissions(workspace:Path,official:list[tuple[str,str]],vals:dict[str,np.ndarray])->tuple[np.ndarray,np.ndarray,dict[str,Any]]:
    m4,mask4,sha4=read_emission(workspace/"ideas/Mx4.parquet",MX4_SHA,"Mx4","F","covariate",official)
    s1,mask1,sha1=read_emission(workspace/"ideas/S1.parquet",S1_SHA,"S1","F","covariate:lag_mx4_f_covariate",official)
    if not(np.array_equal(mask4,vals["mx4_defined"]) and np.array_equal(mask1,vals["s1_defined"])
      and np.array_equal(m4,vals["mx4"],equal_nan=True) and np.array_equal(s1,vals["s1"],equal_nan=True)):
        raise ValueError("entropy artifact frozen Mx4/S1 lag differs from pinned emissions")
    return m4,s1,{"Mx4":{"path":str(workspace/"ideas/Mx4.parquet"),"sha256":sha4},
      "S1_lag":{"path":str(workspace/"ideas/S1.parquet"),"sha256":sha1}}


def run(workspace:Path,report_path:Path)->dict[str,Any]:
    official=[(str(g),str(r)) for g,r in official_scored_pairs(workspace)]
    if len(official)!=EXPECTED_N or len(set(official))!=EXPECTED_N or len({g for g,_ in official})!=EXPECTED_N or set(r for _,r in official)!=set(REGIONS):
        raise ValueError("official support must be exactly 9,379 unique GEOID-region pairs")
    geoids=[g for g,_ in official];regions=np.asarray([r for _,r in official],dtype=object)
    official_sha=hashlib.sha256("\n".join(f"{g},{r}" for g,r in official).encode()).hexdigest()
    vals,entropy_meta=_read_entropy(workspace,official);residual,residual_meta=_read_residual(workspace,official)
    fire,fire_defined,fire_meta=_read_fire(workspace,official);mx4,s1,emission_meta=_feature_emissions(workspace,official,vals)
    road_km=vals["road_km"];logroad=np.full(EXPECTED_N,np.nan);logroad[vals["entropy_defined"]]=np.log(road_km[vals["entropy_defined"]])
    baseline=np.column_stack((logroad,vals["pct_urban"],mx4,s1))
    complete=vals["entropy_defined"]&fire_defined&np.isfinite(residual)&np.isfinite(baseline).all(axis=1)&vals["mx4_defined"]&vals["s1_defined"]
    if int(complete.sum())<100:raise ValueError("insufficient joint entropy-fire-baseline support")
    folds=vals["fold"]
    if np.any((folds<0)|(folds>=N_FOLDS)):raise ValueError("entropy artifact lacks fixed five-fold IDs")
    fit=crossfit_interaction(geoids,regions,folds,residual,baseline,vals["entropy"],fire,complete)
    region_data={};regional={};fire_coverage={}
    for region in REGIONS:
        use=complete&(regions==region)
        if use.sum()<2:raise ValueError(f"insufficient joint support in {region}")
        y0=residual[use];b0=fit["baseline_oof"][use];a0=fit["augmented_oof"][use]
        region_data[region]=(y0,b0,a0);metric=delta_r2(y0,b0,a0)
        fire_coverage[region]={"official_ids":int(np.sum(regions==region)),"registered_fire_defined_ids":int(np.sum((regions==region)&fire_defined)),
          "fire_share_positive_ids":int(np.sum((regions==region)&fire_defined&(fire>0.))),"joint_screen_support":int(use.sum()),
          "joint_fire_positive_ids":int(np.sum(use&(fire>0.))),"fold_support":{str(f):int(np.sum(use&(folds==f))) for f in range(N_FOLDS)}}
        regional[region]=metric
    pooled_y=np.concatenate([region_data[r][0] for r in REGIONS]);pooled_base=np.concatenate([region_data[r][1] for r in REGIONS]);pooled_aug=np.concatenate([region_data[r][2] for r in REGIONS])
    pooled=delta_r2(pooled_y,pooled_base,pooled_aug);bootstrap=bootstrap_deltas(region_data)
    nonfire={r:regional[r]["delta_r2"] for r in NON_FIRE_REGIONS}
    max_nonfire=max(nonfire.values())
    passed=bool(pooled["delta_r2"]>=MIN_DELTA_R2 and bootstrap["pooled_delta_r2_ci_95"][0]>MIN_CI_LOWER and min(nonfire.values())>=MAX_NONFIRE_DECLINE)
    output_hash=array_hash(geoids,regions,[residual,vals["entropy"],fire,*[baseline[:,j] for j in range(4)]],complete)
    report={"schema_version":1,"screen":"Single named-road entropy × provided post-2018 MTBS burned-area interaction against frozen S1 disagreement residual; unregistered",
      "measure_before":{"heat_hinge_pooled_delta_r2":0.0005831039368884199,"heat_hinge_pooled_bootstrap_95_ci":[-0.0008986732365378346,0.002156720503739079],
        "heat_hinge_positive_regions":2,"heat_hinge_report_sha256":sha256_file(ROOT/"reports/named-road-entropy-heat-hinge.json"),
        "fire_interaction_screen_measured":False},
      "method":{"outcome":"Frozen cross-fitted S1 Queen/Rook disagreement residual; no residual refit.",
        "baseline":"OLS with intercept, three official-region indicators, and global slopes for log total named-road km, provided pct_urban, frozen Mx4 F, and frozen S1 lagged Mx4 F.",
        "augmentation":"Add exactly one global interaction term: standardized road-class entropy × standardized Tm2 postfire burned-land share. No standalone entropy/fire terms, nonlinear terms, threshold search, or alternatives are added.",
        "fire_definition":"Use only provided strata fields mtbs_covered, mtbs_wildfire_burned_pct_land, and mtbs_wildfire_last_year, reproducing registered Tm2|F|covariate:postfire_burn_share with the frozen 2018 cutoff. Validate raw-field values/mask against the frozen registered Tm2 covariate artifact.",
        "scaling":"Within each fixed five-fold fit, scale every continuous baseline predictor and each interaction ingredient using training-fold mean and population SD; standardize the product using training-fold product mean/SD. Apply only those train-fitted transforms to held-out tracts.",
        "folds":"Reuse the exact five within-region GEOID folds stored in the frozen named-road entropy artifact. Each fold holds out all rows with its fixed ID; no reallocation.",
        "support":"Paired baseline and augmented predictions use identical official tracts with finite frozen residual, registered entropy, provided postfire covariate, and all baseline covariates. No imputation.",
        "metric":"Pooled and region-specific held-out ΔR²=(baseline SSE - augmented SSE)/held-out residual SST; 2,000 paired tract bootstrap replicates stratified within official region.",
        "nonfire_region_gate":"Treat Maricopa, Eastern Oklahoma, and South-Central Texas as the predeclared non-wildfire-corridor regions; none may have ΔR² < -0.005.",
        "unregistered":True,"interpretation_limit":"Label-free association screen; not target accuracy, causal evidence, or estimator-promotion evidence."},
      "scope":{"official_ids":EXPECTED_N,"official_geoid_region_sha256":official_sha,"entropy_defined_ids":int(vals["entropy_defined"].sum()),
        "fire_defined_ids":int(fire_defined.sum()),"fire_positive_ids":int(np.sum(fire_defined&(fire>0.))),"baseline_complete_ids":int(np.isfinite(baseline).all(axis=1).sum()),
        "joint_complete_case_ids":int(complete.sum()),"joint_null_ids":int((~complete).sum()),"complete_case_mask_sha256":mask_sha256(complete),
        "entropy_mask_sha256":mask_sha256(vals["entropy_defined"]),"fire_mask_sha256":mask_sha256(fire_defined),
        "fixed_fold_assignment_sha256":hashlib.sha256(folds.tobytes()).hexdigest(),
        "fixed_fold_support":{str(f):int(np.sum(complete&(folds==f))) for f in range(N_FOLDS)},
        "region_fire_coverage":fire_coverage,"labels_read":False,"sample_score_values_read":False,"target_reconstruction":False,
        "removed_or_equivalent_references_read":False,"external_data_used":[],"leaderboard_feedback_used":False,"uploads":0,
        "estimator_or_registry_changed":False},
      "measure_after":{"pooled":pooled,"pooled_and_regional_tract_bootstrap":bootstrap,"regional":regional,
        "fold_fit_details":fit["fold_details"],"nonfire_region_delta_r2":nonfire,"worst_nonfire_region_delta_r2":min(nonfire.values()),
        "interaction_values_and_mask_sha256":output_hash},
      "thresholds":{"pooled_delta_r2_minimum_inclusive":MIN_DELTA_R2,"pooled_bootstrap_ci_lower_strictly_above":MIN_CI_LOWER,
        "nonfire_region_delta_r2_minimum_inclusive":MAX_NONFIRE_DECLINE,"bootstrap_replicates":BOOTSTRAP_REPLICATES},
      "acceptance":{"pooled_delta_r2_at_least_0_005":bool(pooled["delta_r2"]>=MIN_DELTA_R2),
        "pooled_tract_bootstrap_ci_lower_above_zero":bool(bootstrap["pooled_delta_r2_ci_95"][0]>MIN_CI_LOWER),
        "no_nonfire_region_decline_below_minus_0_005":bool(min(nonfire.values())>=MAX_NONFIRE_DECLINE),"screen_pass":passed},
      "provenance":{"official_scored_pairs_sha256":official_sha,"entropy_feature":entropy_meta,"frozen_S1_residual":residual_meta,
        "Tm2_provided_fire_covariate":fire_meta,"frozen_baseline_emissions":emission_meta,
        "entropy_values_fire_values_baseline_values_mask_sha256":array_hash(geoids,regions,[vals["entropy"],fire,*[baseline[:,j] for j in range(4)]],complete),
        "analysis_script_sha256":sha256_file(Path(__file__).resolve()),"bootstrap_seed":BOOTSTRAP_SEED,"bootstrap_replicates":BOOTSTRAP_REPLICATES},
      "interpretation":"The preregistered fire interaction passed all held-out gates; diagnostic only and not estimator-promotion evidence." if passed else
        "The preregistered fire interaction failed one or more held-out gates; retain the frozen estimator and keep this screen diagnostic-only."}
    report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--workspace",default=os.environ.get("COMPETITION_WORKSPACE",""));parser.add_argument("--out",default="reports/road-entropy-fire-interaction.json")
    args=parser.parse_args();workspace=Path(args.workspace)
    if not workspace.is_dir():parser.error("COMPETITION_WORKSPACE must be an existing directory")
    report=run(workspace,ROOT/args.out)
    print(json.dumps({"scope":report["scope"],"pooled":report["measure_after"]["pooled"],
      "bootstrap":report["measure_after"]["pooled_and_regional_tract_bootstrap"],"regional":report["measure_after"]["regional"],
      "acceptance":report["acceptance"]},indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
