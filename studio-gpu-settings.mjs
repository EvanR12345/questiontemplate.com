const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export function gpuSettingsForm(config={}) {
  return `<p>Studio waits for an eligible prepared worker, starts one within your project budget, and confirms shutdown after images finish or generation is cancelled. Saving starts no GPU.</p>
  <label>Required GPU<select id="managedGPUType"><option value="">Use the configured worker's GPU</option><option value="NVIDIA GeForce RTX 5090" ${config.runpodRequiredGPUType==='NVIDIA GeForce RTX 5090'?'selected':''}>RTX 5090 only · wait instead of substituting</option>${config.runpodRequiredGPUType && config.runpodRequiredGPUType!=='NVIDIA GeForce RTX 5090'?`<option selected value="${esc(config.runpodRequiredGPUType)}">${esc(config.runpodRequiredGPUType)}</option>`:''}</select></label>
  <label>Maximum wait · minutes<input id="managedWaitMinutes" type="number" min="0" max="1440" value="${esc((config.runpodAvailabilityWaitSeconds??0)/60)}"></label>
  <label>Maximum rental price · US$/hour<input id="managedPrice" type="number" min="0.01" max="100" step=".01" value="${esc(config.runpodMaxHourlyUSD??'')}" placeholder="Leave blank to use the project budget alone"></label>
  <label class="inline"><input id="managedStock" type="checkbox" ${config.runpodCheckRegionalStock?'checked':''}>Check live stock in each prepared worker's region</label>
  <label class="inline"><input id="managedRestoreModels" type="checkbox" ${config.runpodBootstrapKleinPodIds?.length?'checked':''}>Restore verified FLUX.2 Klein 4B model files after restart</label>
  <p class="muted">For prepared workers using temporary disk, model files are downloaded directly to the worker when missing and verified before generation. Cold restoration counts toward rental cost and startup time; no models are downloaded to your laptop.</p>
  <p class="muted">Waiting starts no rental. Availability can change before startup. Failed or uncertain startup is reconciled before another purchase. This improves availability; it does not reserve a 5090.</p>
  <details><summary>Prepared workers and connection</summary><label>Primary existing pod ID<input id="managedPodId" value="${esc(config.runpodManagedPodId||'')}" placeholder="Leave blank for external management"></label>
  <label>Prepared worker pool · one ID per line<textarea id="managedPodPool" rows="3">${esc((config.runpodPreparedPodIds||[]).join('\n'))}</textarea></label>
  <p class="muted">When filled, the pool replaces the primary selection. Only add independently verified workers with the same FLUX workflow and model files. Regional stock alone does not mean a stopped host can restart. A network volume does not move between regions automatically.</p>
  <label>Existing dedicated SSH private-key file<input id="managedSSHFile" value="${esc(config.runpodSSHKeyFile||'')}" placeholder="Absolute path on this laptop"></label></details>
  <p class="muted">Requires your saved Runpod management credential and a project GPU spending cap. Keep the helper awake: its watchdog enforces the deadline. Saving this form creates no pods or downloads; restoration happens only during an authorized generation.</p>`;
}

export function gpuSettingsValues(doc=document) {
  const value=id=>doc.getElementById(id).value.trim();
  const ids=[...new Set(value('managedPodPool').split(/[\s,]+/).filter(Boolean))];
  const primary=value('managedPodId'), required=value('managedGPUType');
  if ([primary,...ids].filter(Boolean).some(id=>!/^[A-Za-z0-9_-]{8,64}$/.test(id)) || ids.length>20) throw Error('Enter valid existing worker IDs, with at most 20 in the pool.');
  if (required && !primary && !ids.length) throw Error('Prepare at least one worker before requiring a GPU type.');
  const waitText=value('managedWaitMinutes'), wait=Number(waitText);
  const priceText=value('managedPrice'), price=priceText?Number(priceText):null;
  if (!waitText || !Number.isFinite(wait) || wait<0 || wait>1440) throw Error('Choose a wait between 0 and 1440 minutes.');
  if (price!==null && (!Number.isFinite(price) || price<.01 || price>100)) throw Error('Choose a positive hourly price limit.');
  return {runpodManagedPodId:primary,runpodPreparedPodIds:ids,runpodRequiredGPUType:required,
    runpodAvailabilityWaitSeconds:wait*60,runpodMaxHourlyUSD:price,
    runpodCheckRegionalStock:doc.getElementById('managedStock').checked,
    runpodBootstrapKleinPodIds:doc.getElementById('managedRestoreModels')?.checked?(ids.length?ids:[primary].filter(Boolean)):[],
    runpodSSHKeyFile:value('managedSSHFile')};
}
