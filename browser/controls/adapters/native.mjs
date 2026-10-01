import {Deferred,Conflict,milliseconds,fieldLocator,normalize,fileMatches} from '../runtime.mjs';
import {access} from 'node:fs/promises';
import {firstEnabledOption} from '../../fallback_policy.mjs';

export const plainText = {
  declaration:{"name": "plain_text_input_v1", "adapterVersion": 1, "targetTypes": ["text"], "protocolVersion": 1},
  async applyField({tab,packet,field,op,end,markAction,readField}) {
    const target=fieldLocator(tab,packet,field);
    markAction();await target.fill(op.value,{timeoutMs:milliseconds(end)});
    if(field.component==='element-date'){
      await target.press('Enter',{timeoutMs:milliseconds(end)});
      await target.evaluate(el=>el.blur());
    }else if(field.component==='ant-text')await target.press('Tab',{timeoutMs:milliseconds(end)});
  }
};

export const checkbox = {
  declaration:{"name": "checkbox_v1", "adapterVersion": 1, "targetTypes": ["boolean"], "protocolVersion": 1},
  async applyField({tab,packet,field,op,end,markAction,readField}) {
    const target=fieldLocator(tab,packet,field);
    markAction();
    if(field.component==='next-checkbox'){
      const label=target.locator('xpath=ancestor::label[1]').locator('.next-checkbox-label');
      if(await label.count()!==1)throw new Conflict('checkbox_label_not_unique');
      await label.click({timeoutMs:milliseconds(end)});
    }else if(typeof target.getAttribute==='function'&&
      /(?:^|\s)el-checkbox__original(?:\s|$)/.test(await target.getAttribute('class',{timeoutMs:milliseconds(end)})||'')){
      const label=target.locator('xpath=ancestor::label[contains(concat(" ",normalize-space(@class)," ")," el-checkbox ")][1]');
      if(await label.count()!==1)throw new Conflict('checkbox_label_not_unique');
      // The input is clipped by Element UI; click its uniquely owned label.
      // The enclosing executor has already compared the checked state.
      await label.click({timeoutMs:milliseconds(end)});
    }else await target.setChecked(op.value,{timeoutMs:milliseconds(end)});

  }
};

export const radio = {
  declaration:{"name": "radio_v1", "adapterVersion": 1, "targetTypes": ["boolean"], "protocolVersion": 1},
  async applyField({tab,packet,field,op,end,markAction,readField}) {
    const target=fieldLocator(tab,packet,field);
    if(op.value!==true)throw new Deferred('cannot_uncheck_radio');markAction();await target.check({timeoutMs:milliseconds(end)});
  }
};

export const radioGroup = {
  declaration:{"name": "radio_group_v1", "adapterVersion": 1, "targetTypes": ["choice"], "protocolVersion": 1},
  async applyField({tab,packet,field,op,end,markAction,readField}) {
    const target=fieldLocator(tab,packet,field);
    const options=field.options.filter(o=>normalize(o.label)===normalize(op.value)&&!o.disabled&&o.selector);
    if(options.length!==1)throw new Deferred('radio_option_missing_or_ambiguous');
    const input=tab.playwright.locator(packet.module_selector).locator(options[0].selector);
    const elementLabel=input.locator('xpath=ancestor::label[contains(concat(" ",normalize-space(@class)," ")," el-radio ")][1]');
    markAction();
    if(await elementLabel.count()===1)await elementLabel.click({timeoutMs:milliseconds(end)});
    else await input.check({timeoutMs:milliseconds(end),force:true});

  }
};

export const fileUpload = {
  declaration:{"name": "file_upload_v1", "adapterVersion": 1, "targetTypes": ["file"], "protocolVersion": 1},
  async applyField({tab,packet,field,op,end,markAction,readField}) {
    const target=fieldLocator(tab,packet,field);
    if(typeof op.value!=='string'||!op.value)throw new Deferred('file_path_required');
    try{await access(op.value);}catch{throw new Deferred('file_path_missing');}
    const matches=f=>fileMatches(f,op.value);
    if(matches(field))return;
    markAction();await target.setInputFiles(op.value,{timeoutMs:milliseconds(end)});
    const uploadEnd=Math.min(end,Date.now()+20_000);let uploaded=null;
    do{
      uploaded=await readField(uploadEnd);
      if(matches(uploaded?.field))break;
      await new Promise(resolve=>setTimeout(resolve,100));
    }while(Date.now()<uploadEnd);
    if(!uploaded?.field||!matches(uploaded.field))throw new Conflict('file_upload_not_ready');

  }
};

export const nativeSelect = {
  declaration:{"name": "native_select_v1", "adapterVersion": 1, "targetTypes": ["choice", "choice_set"], "protocolVersion": 1},
  async applyField({tab,packet,field,op,end,markAction,readField}) {
    const target=fieldLocator(tab,packet,field);
    if(op.fallback==='first_option'){
      const first=firstEnabledOption(field.options);
      if(!first)throw new Deferred('fallback_no_available_option');
      op.value=field.multiple?[first.label]:first.label;
    }
    const wanted=Array.isArray(op.value)?op.value:[op.value];
    ({field}=await readField(end));
    if(!wanted.every(v=>field.options.filter(o=>o.label===v&&!o.disabled).length===1)){
      const error=new Deferred('option_missing_or_ambiguous');
      const options=[...new Set(field.options.filter(o=>!o.disabled&&o.label.trim()).map(o=>o.label))];
      if(typeof op.value==='string'&&options.length)error.enumCandidate={field_id:op.id,source_value:op.value,options};
      throw error;
    }
    markAction();await target.selectOption(wanted.map(label=>({label})),{timeoutMs:milliseconds(end)});

  }
};
