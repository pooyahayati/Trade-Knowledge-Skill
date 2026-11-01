#property strict
// Fixture SHA256: 6843c0e8ed17225190bb767e33c7ced080d716666c67fa427c8fddfbef13d226
// Scope: 12 numeric fields and 3 category fields; fixed eligible input only.
// Trade Knowledge 0.8.0: stable_indicator_conformance_v1 arithmetic only.
// No platform ATR call, orders, clocks, pivots or chart access in this kernel.
#ifndef TRADE_KNOWLEDGE_CORE_MQH
#define TRADE_KNOWLEDGE_CORE_MQH

struct TKState {
   int period, count;
   bool previous_ready, atr_ready;
   double previous, sum, atr, seed_weight, quantum;
};
struct TKRow {
   bool has_tr, has_atr, has_baseline, has_ratio, has_fraction;
   double tr, atr, baseline, span, body, upper, lower;
   double seed, raw, comp, bf, cl;
   // Length: -1 unknown, 0 flat, 1 short, 2 standard, 3 long, 4 very long.
   // Candidates: -1 unknown, 0 false, 1 true. Unknown is never false.
   int length, dominant, pin;
};
bool TKReset(TKState &s, const int period, const double quantum) {
   s.period=period; s.count=0; s.previous_ready=false; s.atr_ready=false;
   s.previous=0; s.sum=0; s.atr=0; s.seed_weight=0; s.quantum=quantum;
   return period>=1 && period<=5000 && MathIsValidNumber(quantum) && quantum>0;
}
void TKEmpty(TKRow &r) {
   r.has_tr=false; r.has_atr=false; r.has_baseline=false;
   r.has_ratio=false; r.has_fraction=false;
   r.tr=0; r.atr=0; r.baseline=0; r.span=0; r.body=0; r.upper=0;
   r.lower=0; r.seed=0; r.raw=0; r.comp=0; r.bf=0; r.cl=0;
   r.length=-1; r.dominant=-1; r.pin=-1;
}
double TKSnap(const double x, const double boundary) {
   return MathAbs(x-boundary)<=1e-9 ? boundary : x;
}
double TKDelta(const double a,const double b,const double quantum) {
   // Inputs must share this price grid. Restore exact tick multiples after
   // cancellation of large prices; never round ATR or dimensionless ratios.
   return MathRound((a-b)/quantum)*quantum;
}
bool TKOnGrid(const double price,const double quantum) {
   double units=price/quantum;
   return MathAbs(units-MathRound(units))<=MathMax(1e-9,MathAbs(units)*4e-16);
}
bool TKStep(TKState &s, const double o, const double h,
            const double l, const double c, TKRow &r) {
   TKEmpty(r);
   // Reject without advancing state. Driver must halt at the first bad bar.
   if(s.period<1 || s.period>5000 || !MathIsValidNumber(s.quantum) || s.quantum<=0 || !MathIsValidNumber(o) ||
      !MathIsValidNumber(h) || !MathIsValidNumber(l) || !MathIsValidNumber(c) ||
      l<=0 || h<l || o<l || o>h || c<l || c>h || h/s.quantum>1e14) return false;
   if(!TKOnGrid(o,s.quantum) || !TKOnGrid(h,s.quantum) ||
      !TKOnGrid(l,s.quantum) || !TKOnGrid(c,s.quantum)) return false;
   r.span=TKDelta(h,l,s.quantum); r.body=MathAbs(TKDelta(c,o,s.quantum));
   r.upper=TKDelta(h,MathMax(o,c),s.quantum); r.lower=TKDelta(MathMin(o,c),l,s.quantum);
   if(s.atr_ready) {
      r.has_baseline=true; r.baseline=s.atr; r.seed=s.seed_weight;
   }
   if(s.previous_ready) {
      r.has_tr=true;
      r.tr=MathMax(r.span,MathMax(MathAbs(TKDelta(h,s.previous,s.quantum)),MathAbs(TKDelta(l,s.previous,s.quantum))));
      if(s.atr_ready) {
         s.atr=((s.period-1)*s.atr+r.tr)/s.period;
         s.seed_weight*=((double)(s.period-1))/s.period;
      } else {
         s.sum+=r.tr; s.count++;
         if(s.count==s.period) {
            s.atr=s.sum/s.period; s.atr_ready=true; s.seed_weight=1;
         }
      }
   }
   s.previous=c; s.previous_ready=true;
   r.has_atr=s.atr_ready; r.atr=s.atr;
   if(r.span==0) {
      if(r.has_baseline && r.baseline>0) {
         r.has_ratio=true; r.raw=0; r.comp=0;
      }
      r.length=0; r.dominant=0; r.pin=0;
   } else {
      r.has_fraction=true; r.bf=r.body/r.span; r.cl=TKDelta(c,l,s.quantum)/r.span;
      if(r.has_baseline && r.baseline>0) {
         r.has_ratio=true; r.raw=r.span/r.baseline;
         r.comp=TKSnap(TKSnap(TKSnap(r.raw,0.8),1.2),2.5);
         r.length=r.comp<0.8 ? 1 : (r.comp<=1.2 ? 2 : (r.comp<=2.5 ? 3 : 4));
         double part=MathMax(r.body,MathMax(r.upper,r.lower))/r.span;
         r.dominant=(r.length==2 && TKSnap(part,0.8)>=0.8) ? 1 : 0;
         bool top=TKSnap(r.lower/r.span,0.8)>=0.8 && TKSnap(r.upper/r.span,0.1)<=0.1;
         bool bottom=TKSnap(TKDelta(MathMax(o,c),l,s.quantum)/r.span,0.2)<=0.2 && TKSnap(r.lower/r.span,0.1)<=0.1;
         r.pin=(r.comp>=1.2 && (top || bottom)) ? 1 : 0;
      }
   }
   return true;
}
// Minutes are supplied explicitly by the wrapper, including calendar months.
int TKPeriod(const int minutes, const int custom) {
   int fixed=0;
   if(minutes==60) fixed=24;
   if(minutes==240 || minutes==1440) fixed=30;
   if(minutes==10080) fixed=54;
   if(minutes==43200) fixed=12;
   if(fixed>0) return (custom==0 || custom==fixed) ? fixed : 0;
   if(minutes==1 || minutes==5 || minutes==15)
      return (custom>=1 && custom<=5000) ? custom : 0;
   return 0;
}
#endif

// Self-contained script: attach to any chart; reads no market/account data.
double prices[][4]={
{100,100,100,100},
{100,100,100,100},
{100,100,100,100},
{130,132,130,131},
{131,131,131,131}
};
double expected[][15]={
{0,0,0,0,0,0,0,0,0,0,0,0,0,0,0},
{0,0,0,0,0,0,0,0,0,0,0,0,0,0,0},
{0,0,0,0,0,0,0,0,0,0,0,0,0,0,0},
{32,16,0,2,1,1,0,1,0,0,0.5,0.5,-1,-1,-1},
{0,8,16,0,0,0,0,0.5,0,0,0,0,0,0,0}
};
int null_masks[]={3975,3974,3972,768,3072};
int TKFixtureCheck() {
   TKState state; TKReset(state,2,0.00000000001); int failures=0;
   for(int i=0;i<5;i++) {
      TKRow r;
      if(!TKStep(state,prices[i][0],prices[i][1],prices[i][2],prices[i][3],r)) return -1;
      double actual[15];
      actual[0]=r.tr;
      actual[1]=r.atr;
      actual[2]=r.baseline;
      actual[3]=r.span;
      actual[4]=r.body;
      actual[5]=r.upper;
      actual[6]=r.lower;
      actual[7]=r.seed;
      actual[8]=r.raw;
      actual[9]=r.comp;
      actual[10]=r.bf;
      actual[11]=r.cl;
      actual[12]=r.length;
      actual[13]=r.dominant;
      actual[14]=r.pin;
      int missing=0;
      if(!r.has_tr) missing|=1;
      if(!r.has_atr) missing|=2;
      if(!r.has_baseline) missing|=4;
      if(!r.has_baseline) missing|=128;
      if(!r.has_ratio) missing|=256;
      if(!r.has_ratio) missing|=512;
      if(!r.has_fraction) missing|=1024;
      if(!r.has_fraction) missing|=2048;
      for(int j=0;j<15;j++) {
         bool want_null=(null_masks[i] & (1<<j))!=0;
         bool is_null=(missing & (1<<j))!=0;
         if(want_null!=is_null) { failures++; continue; }
         if(want_null) continue;
         double absolute=j<7 ? 0.00000000000000001 : (j<12 ? 1e-12 : 0);
         double tolerance=j<12 ? MathMax(absolute,MathAbs(expected[i][j])*1e-12) : 0;
         if(MathAbs(actual[j]-expected[i][j])>tolerance) failures++;
      }
   }
   return failures;
}
void OnStart() {
   int failures=TKFixtureCheck();
   Print("TK fixture zero-seed-gap-flat"," rows=5 mismatches=",failures);
}
