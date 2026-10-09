// Public Pod listings checked against the official source. These are estimates,
// not authenticated deployment quotes. Preserve immutable benchmark receipts.
export const PRICING_DATE='2026-10-09';
export const PRICING_URL='https://www.runpod.io/pricing';
export const POD_RATES={
  '5090':1.19,'4090':.89,'3090':.50,'a5000':.27,'pro96':2.49,'pro6000':2.49,
  'mig48':1.29,'mig24':.69,'l40s':1.09,'ada6000':.99,'a40':.59,'a6000':.59,
  'gpu-l40':.82,'l4':.59,'a100pcie':1.79,'a100sxm':1.79,'h100pcie':2.89,
  'gpu-h100-nvl':3.19,'gpu-h200':5.29,'gpu-b200':7.99,'gpu-b300-sxm6-ac':8.99,
};
export function priceGPU(gpu,config){
  const historicalHourly=gpu.historicalHourly??gpu.hourly;
  const diskHourly=config.containerGB*.10/(30*24);
  const quoted=config.pricingBasis==='quote'&&config.quotedGPU===gpu.id&&config.quotedGPUHourly>0;
  const published=POD_RATES[gpu.id];
  if(quoted||config.pricingBasis!=='receipt'&&published!==undefined){
    const computeHourly=quoted?config.quotedGPUHourly:published;
    return {...gpu,historicalHourly,hourly:computeHourly+diskHourly,pricing:{
      basis:quoted?'quote':'published',computeHourly,containerHourly:diskHourly,date:PRICING_DATE,
      note:quoted?'Your entered compute quote plus configured running container disk. Confirm the quote excludes disk before entering it.':
        `Public Pod listing checked ${PRICING_DATE}; not a live region/account quote. Running container disk uses ${config.containerGB} GB at $0.10/GB/month, with a 30-day planning month.`,
    }};
  }
  return {...gpu,historicalHourly,pricing:{basis:'receipt',date:gpu.date,note:
    config.pricingBasis==='receipt'?'Historical measured compute + running container disk receipt. Retained storage is separate.':
    'No unambiguous current public listing for this GPU/partition. Using its historical compute + running container receipt; confirm a deployment quote before budgeting.'}};
}
export function costParts(rentalSeconds,hourly,directorUSD,qcUSD,storageDaily,storageDays=1){
  const gpuUSD=rentalSeconds/3600*hourly,apiUSD=directorUSD+qcUSD;
  const productionUSD=gpuUSD+apiUSD,storageUSD=storageDaily*storageDays;
  return {gpuUSD,apiUSD,productionUSD,storageUSD,totalUSD:productionUSD+storageUSD};
}
