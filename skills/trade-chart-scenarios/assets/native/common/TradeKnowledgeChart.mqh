// Shared MT4/MT5 chart adapter. Requires the normal terminal series (index 0 live).
// Main .mq4/.mq5 includes TradeKnowledgeCore.mqh before this file.
input datetime HistoryAnchor=0; // Required exact bar-open time in broker chart time
input int CustomPeriod=0;       // 0 uses Excel profile; M1/M5/M15 require 1..5000
input double PriceQuantum=0.0;  // 0 uses chart precision (_Point), not order tick size
double Baseline[],Span[],AfterATR[],TrueRange[],Length[],Dominant[],Pin[],Seed[];
datetime TKLastOpen=0,TKFirstOpen=0;
int TKSelectedPeriod=0;
double TKQuantum=0;

void TKClear() {
   ArrayInitialize(Baseline,EMPTY_VALUE); ArrayInitialize(Span,EMPTY_VALUE);
   ArrayInitialize(AfterATR,EMPTY_VALUE); ArrayInitialize(TrueRange,EMPTY_VALUE);
   ArrayInitialize(Length,EMPTY_VALUE); ArrayInitialize(Dominant,EMPTY_VALUE);
   ArrayInitialize(Pin,EMPTY_VALUE); ArrayInitialize(Seed,EMPTY_VALUE);
}
void TKPlot(const int i,const string label,const bool visible,const color ink) {
#ifdef __MQL5__
   PlotIndexSetInteger(i,PLOT_DRAW_TYPE,visible ? DRAW_LINE : DRAW_NONE);
   PlotIndexSetInteger(i,PLOT_LINE_COLOR,ink);
   PlotIndexSetDouble(i,PLOT_EMPTY_VALUE,EMPTY_VALUE);
   PlotIndexSetString(i,PLOT_LABEL,label);
#else
   SetIndexStyle(i,visible ? DRAW_LINE : DRAW_NONE,STYLE_SOLID,1,ink);
   SetIndexEmptyValue(i,EMPTY_VALUE); SetIndexLabel(i,label);
#endif
}
int OnInit() {
   // PeriodSeconds maps MN1 to the platform's nominal 30-day period here only.
   TKSelectedPeriod=TKPeriod(PeriodSeconds(_Period)/60,CustomPeriod);
   TKQuantum=PriceQuantum==0 ? _Point : PriceQuantum;
   if(HistoryAnchor<=0 || TKSelectedPeriod==0 || !MathIsValidNumber(TKQuantum) || TKQuantum<=0) {
      Print("Choose an exact history anchor and supported timeframe/period");
      return INIT_PARAMETERS_INCORRECT;
   }
   SetIndexBuffer(0,Baseline,INDICATOR_DATA); SetIndexBuffer(1,Span,INDICATOR_DATA);
   SetIndexBuffer(2,AfterATR,INDICATOR_DATA); SetIndexBuffer(3,TrueRange,INDICATOR_DATA);
   SetIndexBuffer(4,Length,INDICATOR_DATA); SetIndexBuffer(5,Dominant,INDICATOR_DATA);
   SetIndexBuffer(6,Pin,INDICATOR_DATA); SetIndexBuffer(7,Seed,INDICATOR_DATA);
   ArraySetAsSeries(Baseline,true); ArraySetAsSeries(Span,true);
   ArraySetAsSeries(AfterATR,true); ArraySetAsSeries(TrueRange,true);
   ArraySetAsSeries(Length,true); ArraySetAsSeries(Dominant,true);
   ArraySetAsSeries(Pin,true); ArraySetAsSeries(Seed,true);
   TKPlot(0,"Previous closed ATR",true,clrBlue); TKPlot(1,"Candle range",true,clrGray);
   TKPlot(2,"ATR after bar",false,clrBlue); TKPlot(3,"True range",false,clrGray);
   TKPlot(4,"Length code",false,clrGray); TKPlot(5,"Dominant geometry candidate",false,clrGray);
   TKPlot(6,"Pinbar geometry candidate",false,clrGray); TKPlot(7,"Seed weight",false,clrGray);
#ifdef __MQL5__
   IndicatorSetString(INDICATOR_SHORTNAME,"Trade Knowledge Foundation 0.12.0 (closed bars)");
   IndicatorSetInteger(INDICATOR_DIGITS,12);
#else
   IndicatorShortName("Trade Knowledge Foundation 0.12.0 (closed bars)"); IndicatorDigits(12);
#endif
   TKLastOpen=0; TKFirstOpen=0;
   return INIT_SUCCEEDED;
}
int OnCalculate(const int rates_total,const int prev_calculated,
                const datetime &time[],const double &open[],const double &high[],
                const double &low[],const double &close[],const long &tick_volume[],
                const long &volume[],const int &spread[]) {
   ArraySetAsSeries(time,true); ArraySetAsSeries(open,true); ArraySetAsSeries(high,true);
   ArraySetAsSeries(low,true); ArraySetAsSeries(close,true);
   if(rates_total<2) { TKClear(); return 0; }
   // Terminal resets prev_calculated to zero after history corrections/backfill.
   if(prev_calculated==rates_total && TKLastOpen==time[0] && TKFirstOpen==time[rates_total-1])
      return rates_total;
   TKClear(); TKState state; TKReset(state,TKSelectedPeriod,TKQuantum);
   bool started=false; datetime last=0;
   // Index zero is conservatively withheld even when the market is closed.
   for(int i=rates_total-1;i>=1;i--) {
      if(time[i]<HistoryAnchor) continue;
      if(!started) {
         if(time[i]!=HistoryAnchor) {
            Print("Exact anchor absent from loaded history"); return 0;
         }
         started=true;
      }
      TKRow row;
      if(time[i]<=last || !TKStep(state,open[i],high[i],low[i],close[i],row)) {
         TKClear(); Print("Invalid OHLC or bar order; calculation stopped"); return 0;
      }
      last=time[i]; Span[i]=row.span; Length[i]=row.length;
      if(row.has_baseline) { Baseline[i]=row.baseline; Seed[i]=row.seed; }
      if(row.has_atr) AfterATR[i]=row.atr;
      if(row.has_tr) TrueRange[i]=row.tr;
      if(row.dominant>=0) Dominant[i]=row.dominant;
      if(row.pin>=0) Pin[i]=row.pin;
   }
   TKLastOpen=time[0]; TKFirstOpen=time[rates_total-1];
   return rates_total;
}
