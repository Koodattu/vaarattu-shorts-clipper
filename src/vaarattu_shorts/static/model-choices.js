"use strict";
let workspaceModel={choice:"default",budget_usd:null}, modelProfiles={};
const providerNames={local:"Local · Gemma 4",gemini:"Gemini 3.8 Flash",zai:"GLM-5.3-Flash",deepseek:"DeepSeek V4 Flash",meta:"Meta Muse Spark 1.3"};
function modelLabel(profile){
  if(profile.provider==="codex")return "Codex "+(profile.codex_model||"gpt-6-luna");
  if(profile.provider==="openai")return "OpenAI API - "+(profile.openai_model||"gpt-6-luna");
  if(profile.provider==="local"&&profile.local_model)return profile.local_model==="gemma4-31b"?"Local · Gemma 4 31B":"Local · Gemma 4 26B-A4B";
  return providerNames[profile.provider]||profile.provider;
}
function modelOptions(status,expandLocal=false){
  modelProfiles=status.llm_profiles||modelProfiles;
  const options=[];
  for(const [provider,info] of Object.entries(status.providers)){
    const entries=Object.entries(modelProfiles).filter(([,p])=>p.provider===provider);
    for(const [key,profile] of (entries.length&&(provider!=="local"||expandLocal)?entries:[[provider,{provider}]])){
      const missing=expandLocal&&profile.local_model&&!status.models[profile.local_model];
      const reason=!info.available?"unavailable":!info.configured?"key missing":missing?"not prepared":"";
      options.push({key,profile,label:modelLabel(profile)+(reason?" · "+reason:""),disabled:Boolean(reason)});
    }
  }
  return options;
}
function effectiveModel(key){
  const selected=workspaceModel.choice==="default"?key:workspaceModel.choice;
  return modelProfiles[selected]||{provider:selected};
}
function modelFields(profile){
  if(profile.codex_model&&profile.codex_model!=="gpt-6-luna")return {codex_model:profile.codex_model};
  if(profile.openai_model&&profile.openai_model!=="gpt-6-luna")return {openai_model:profile.openai_model};
  return profile.local_model?{local_model:profile.local_model}:{};
}
function usesApi(provider){return !["local","codex"].includes(provider);}
function modelBudget(field){return workspaceModel.choice==="default"?Number($(field).value):workspaceModel.budget_usd||0;}
