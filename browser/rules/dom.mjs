// This function is serialized into a READ-ONLY DOM scope. Do not add writes here.
export function readModuleDOM({moduleSelector, saveControl = null, saveLabel = null, savedSignal = null}) {
  const roots = [...document.querySelectorAll(moduleSelector)];
  if (roots.length !== 1) return {error:'module_not_unique', url:location.href};
  const root = roots[0];
  const visible = el => {
    if(!el.getClientRects().length||getComputedStyle(el).visibility==='hidden')return false;
    // Zero-sized clipping ancestors hide upload validation sentinels even when
    // the input itself still has a layout rectangle.
    for(let p=el.parentElement;p;p=p.parentElement){
      const style=getComputedStyle(p);
      if(/hidden|clip/.test(style.overflow)){
        const r=p.getBoundingClientRect();if(!r.width||!r.height)return false;
      }
    }
    return true;
  };
  const blockingDialogs=[...document.querySelectorAll('[role="dialog"],.el-dialog,.el-message-box')]
    .filter(el=>visible(el)&&el!==root&&!el.contains(root));
  const protectedRE = /(?!)/; // Personal fields use the ordinary value path.
  const text = el => el?.textContent?.trim() || '';
  const labelOf = el => {
    const gradeProof=el.type==='file'&&el.closest('.grade-prove__wrapper');
    if(gradeProof&&gradeProof.querySelectorAll('input[type=file]').length===1&&
       text(gradeProof.querySelector('.grade-prove__add-text'))==='添加成绩证明')return '成绩证明';
    const explicit=el.getAttribute('data-form-field-i18n-name');
    if(explicit)return explicit.trim();
    const moka=el.closest('[class*="apply-field-"]');
    const phoenix=el.closest('.form-item--phoenix');
    const united=el.closest('.ud-formily-item');
    const structured=moka||phoenix||united;
    if(structured){
      const title=moka?moka.querySelector(':scope > [class*="title-"]'):
        phoenix?phoenix.querySelector('.form-item__text,.form-item__title'):
        united.querySelector('.ud-formily-item-label');
      const base=text(title).replace(/\*\s*$/,'').trim();
      if(base){
        if(['checkbox','radio'].includes(el.type))return text(el.closest('label'))||base;
        const udRange=el.closest('.throne-biz-date-range-picker-wrapper');
        const udInputs=[...(udRange?.querySelectorAll('input.ud__native-input')||[])];
        if(udInputs.length===2&&udInputs.includes(el))return base+(udInputs.indexOf(el)===0?' 开始时间':' 结束时间');
        const monthRange=el.closest('.month-range-select');
        const selects=monthRange?[...monthRange.querySelectorAll('[class*="sd-Select-container"] input')]:
          [...structured.querySelectorAll('input')].filter(x=>['年','月'].includes(x.getAttribute('placeholder')));
        if(selects.includes(el)){
          const index=selects.indexOf(el),part=monthRange&&[2,4].includes(selects.length)?(index%2===0?'年':'月'):el.getAttribute('placeholder');
          return base+(selects.length===4?(index<2?' 开始':' 结束'):'')+' '+part;
        }
        if(/手机/.test(base)&&el.closest('[class*="sd-Select-container"],.phoenix-select'))return '手机 国家/地区代码';
        if(/证件/.test(base)&&el.closest('[class*="sd-Select-container"],.phoenix-select'))return '证件类型';
        return base;
      }
    }
    const nextItem=el.closest('.next-form-item');
    const nextLabel=nextItem?.querySelector('.next-form-item-label label');
    if(nextLabel){
      const base=text(nextLabel).replace(/\*\s*$/,'').trim();
      if(!base){
        if(['checkbox','radio'].includes(el.type))return text(el.closest('label'))||el.name||el.id;
        if(/\.languageRank$/.test(el.name||el.id||''))return '语言熟练程度';
        return el.getAttribute('placeholder')||el.name||el.id||'';
      }
      if(el.closest('.next-range-picker')&&['起始日期','结束日期'].includes(el.getAttribute('placeholder')))
        return base+' '+el.getAttribute('placeholder');
      if(/手机|mobile|phone/i.test(base)&&nextItem.querySelector('.next-number-picker')&&nextItem.querySelector('.next-select'))
        return el.closest('.next-select')?base+' 国家/地区代码':base+'号码';
      return base;
    }
    const antItem=el.closest('.ant-form-item');
    const antLabel=antItem?.querySelector('.ant-form-item-label > label');
    if(antLabel){
      const base=text(antLabel).replace(/\*\s*$/,'').trim();
      if(/手机|mobile|phone/i.test(base)&&((el.closest('.ant-select')&&
          [...antItem.querySelectorAll('input')].some(n=>!n.closest('.ant-select')&&n.type!=='hidden'))||
          (el.getAttribute('role')==='combobox'&&antItem.querySelector('input[placeholder*="手机"]')&&!el.contains(antItem.querySelector('input[placeholder*="手机"]')))))
        return '手机 国家/地区代码';
      if(el.closest('.ant-picker')&&/^(开始|结束)时间$/.test(el.getAttribute('placeholder')||''))return base+' '+el.getAttribute('placeholder');
      return base;
    }
    let item=el.closest('.el-form-item');
    while(item&&!item.querySelector(':scope > label')&&!item.querySelector(':scope > .el-form-item__label'))item=item.parentElement?.closest('.el-form-item');
    const itemLabel=item?.querySelector(':scope > label')||item?.querySelector(':scope > .el-form-item__label');
    if(itemLabel){
      const base=text(itemLabel).replace(/\*\s*$/,'').trim();
      // A checkbox or radio is one option inside the business field.  Keeping
      // only the enclosing form-item label collapses every option to the same
      // semantic identity and can make a planner write or skip the wrong one.
      const optionLabel=['checkbox','radio'].includes(el.type)?text(el.closest('label')):'';
      if(optionLabel&&optionLabel!==base)return (base+' '+optionLabel).trim();
      // Phone widgets commonly place a country/region-code select and the
      // actual number input in one form item.  Their enclosing label is the
      // same, so distinguish the two controls from their stable structure.
      const controls=[...(item.querySelectorAll?.('input,textarea,select,[role="combobox"]')||[])];
      const phoneItem=/手机|电话|mobile|phone/i.test(base)&&controls.some(control=>control.closest('.el-select'))&&
        controls.some(control=>!control.closest('.el-select')&&
          /手机|电话|mobile|phone/i.test([control.getAttribute('placeholder'),control.getAttribute('name'),control.getAttribute('aria-label')].join(' ')));
      if(phoneItem&&controls.length>1){
        if(el.closest('.el-select'))return base+' 国家/地区代码';
        if(controls.some(other=>other!==el&&other.closest('.el-select')))return base+' 号码正文';
      }
      if(/手机|电话/.test(base)&&item.querySelector('.el-input-group__prepend .el-select'))return el.closest('.el-select')?base+' 国家/地区代码':base+'号码';
      if(el.closest('.certificate-validate'))return el.closest('.el-select')?'证件类型':'证件号码';
      if(/籍贯/.test(base)){const levels=[...item.querySelectorAll('.el-select')];const current=el.closest('.el-select');if(levels.length>1&&current)return base+' 第'+(levels.indexOf(current)+1)+'级';}
      return base;
    }
    const moduleHeading=el.closest('.module-title')?.querySelector('.module-title__label');
    if(moduleHeading)return text(moduleHeading).replace(/必填$/,'').trim();
    if (el.labels?.length && [...el.labels].some(l=>text(l))) return [...el.labels].map(text).join(' ');
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    if (ids.length) return ids.map(id=>text(document.getElementById(id))).join(' ');
    return el.getAttribute('placeholder') || el.getAttribute('name') || el.id || '';
  };
  const selectorOf = el => {
    const candidates = [];
    if (el.id) candidates.push('#'+CSS.escape(el.id));
    for (const attr of ['name','aria-label','placeholder','data-testid']) {
      // Element UI mutates an input placeholder while a selected value is
      // focused (for example, "请选择" <-> "中共党员").  That attribute is
      // display state, not DOM identity, so use the checked structural path
      // for these controls instead of compiling a selector that expires as
      // soon as the menu closes.
      if(attr==='placeholder'&&el.closest('.el-select,.ant-select,[class*="sd-Select-container"],.phoenix-select,.atsx-select,.ud__select,label.day_info'))continue;
      const v = el.getAttribute(attr);
      if (v) candidates.push(el.tagName.toLowerCase()+'['+attr+'='+JSON.stringify(v)+']');
    }
    const direct=candidates.find(s=>root.querySelectorAll(s).length===1);
    if(direct)return direct;
    // A structural path is used only with a freshly checked semantic signature.
    const parts=[];
    let current=el;
    while(current&&current!==root){
      const peers=[...current.parentElement.children].filter(n=>n.tagName===current.tagName);
      parts.unshift(current.tagName.toLowerCase()+':nth-of-type('+(peers.indexOf(current)+1)+')');
      current=current.parentElement;
    }
    const path=parts.join(' > ');
    return current===root&&root.querySelectorAll(':scope > '+path).length===1?':scope > '+path:null;
  };
  const documentPath = el => {
    if(el.id&&document.querySelectorAll('#'+CSS.escape(el.id)).length===1)return '#'+CSS.escape(el.id);
    const parts=[];let current=el;
    while(current&&current!==document.body){
      const peers=[...current.parentElement.children].filter(n=>n.tagName===current.tagName);
      parts.unshift(current.tagName.toLowerCase()+':nth-of-type('+(peers.indexOf(current)+1)+')');current=current.parentElement;
    }
    return current===document.body?'body > '+parts.join(' > '):null;
  };
  const educationRowSelector='.education-edit-item__wrapper';
  const educationRows=root.matches(educationRowSelector)?[root]:[...root.querySelectorAll(educationRowSelector)];
  const educationCollections=[...new Set(educationRows.map(row=>row.parentElement)
    .filter(parent=>parent?.matches('.resume-first-education-edit-item__wrapper')))];
  const recordBoundary=educationCollections.length===1?(()=>{
    const collection=educationCollections[0];
    const siblings=[...collection.querySelectorAll(':scope > .education-edit-item__wrapper')];
    return {container_selector:documentPath(collection),record_selector:':scope > .education-edit-item__wrapper',
      record_count:siblings.length,record_index:root.matches(educationRowSelector)?siblings.indexOf(root):null,
      scope_record_count:educationRows.length};
  })():null;
  const groupSelector='.ant-radio-group,.el-radio-group,[role="radiogroup"],.ud__radio-group,.phoenix-radio-group';
  const groups=[...root.querySelectorAll(groupSelector)].filter(el=>el.querySelector('input[type="radio"],.phoenix-radio'));
  const implicitRadioGroups=[...root.querySelectorAll('.el-form-item__content')].filter(el=>{
    const controls=[...el.querySelectorAll('input,textarea,select,[role="combobox"]')];
    return controls.length>=2&&controls.every(c=>c.type==='radio'&&c.closest('label.el-radio'))&&
      !el.querySelector('.el-form-item')&&!groups.some(g=>g.contains(el)||el.contains(g));
  });
  groups.push(...implicitRadioGroups);
  const nodes = [...new Set([...root.querySelectorAll('input,textarea,select,[role="combobox"],'+groupSelector),...implicitRadioGroups])].filter(el=>{
    if (['hidden','button','submit','reset'].includes(el.type)) return false;
    if(el.type==='radio'&&groups.some(group=>group.contains(el)))return false;
    if(el.matches('[role="combobox"].el-date-editor')&&el.querySelector('input'))return false;
    const combo = el.parentElement?.closest('[role="combobox"]');
    return !combo || !root.contains(combo)||combo.matches('.el-date-editor');
  });
  // Native file inputs are intentionally hidden behind an upload button. They
  // remain valid Playwright controls and must be observed even when the input
  // itself has no layout box.
  const fields = nodes.filter(el=>visible(el)||el.type==='file').map((el,index)=>{
    const label=labelOf(el), selector=selectorOf(el), role=el.getAttribute('role') || '';
    const autocomplete=(el.getAttribute('aria-autocomplete')||'').toLowerCase();
    const customHint=el.hasAttribute('list')||['list','inline','both'].includes(autocomplete)||el.hasAttribute('aria-haspopup')||
      !!el.closest('.el-select,.el-cascader,.el-autocomplete,.el-date-editor')||
      /select|cascader|autocomplete/i.test([el.className,el.parentElement?.className].join(' '));
    const elementSelect=el.tagName==='INPUT'&&!!el.closest('.el-select');
    const nextSelect=el.tagName==='INPUT'&&!!el.closest('.next-select');
    const antSelect=el.tagName==='INPUT'&&!!el.closest('.ant-select')&&!!el.closest('.ant-select').querySelector('.ant-select-selector');
    const mokaSelect=el.tagName==='INPUT'&&!!el.closest('[class*="sd-Select-container"]');
    const phoenixOwner=el.closest('.phoenix-select');
    const phoenixDate=!!phoenixOwner?.querySelector('use[*|href*="field_date_time_picker"],path[id*="field_date_time_picker"]');
    const phoenixSelect=el.tagName==='INPUT'&&!!phoenixOwner&&!phoenixDate;
    const atsxSelect=role==='combobox'&&!!el.closest('.atsx-select');
    const udSelect=el.tagName==='INPUT'&&!!el.closest('.ud__select')?.querySelector('.ud__select__selector,.ud__select__selector__search,.ud__select__selector__selection,.ud__select__selector__selectItem');
    const frameworkSelect=antSelect||mokaSelect||phoenixSelect||atsxSelect||udSelect;
    const dateComponent=el.closest('.ant-picker')?'ant-picker':phoenixDate?'phoenix-date':el.closest('label.day_info,[class*="sd-DatePicker"]')?'moka-date':el.closest('.ud__picker')?'ud-date':el.closest('.throne-biz-date-range-picker-wrapper')?'ud-range-date':null;
    const frameworkDate=el.tagName==='INPUT'&&!!dateComponent;
    const hierarchy=el.readOnly&&!!el.closest('[class*="sd-Input-tag-container"]')&&!mokaSelect;
    const frameworkText=!frameworkSelect&&!frameworkDate&&(el.matches('.ud__native-input,.phoenix-input__input,.phoenix-textarea__realTextarea')||
      el.matches('[class*="sd-Input-common-input"],[class*="sd-Textarea-textarea"]')&&!el.readOnly);
    // Element UI date inputs are commonly readonly because the calendar owns
    // the interaction. They are still writable through the verified calendar
    // adapter and must not be classified as disabled text.
    const elementDate=el.tagName==='INPUT'&&el.type==='text'&&!!el.closest('.el-date-editor');
    const elementDateNow=elementDate&&!!el.closest('.apply-form-date-now');
    const antDate=el.tagName==='INPUT'&&el.matches('.ant-calendar-picker-input')&&!!el.closest('.ant-calendar-picker');
    const radioGroup=groups.includes(el);
    const kind=radioGroup?'radio_group':el.type==='file'?'file':el.tagName==='SELECT'?'select':role==='combobox'||elementSelect||frameworkSelect?'combobox':
      el.tagName==='TEXTAREA'||elementDate||antDate||frameworkDate||hierarchy?'text':['checkbox','radio'].includes(el.type)?el.type:
      ['text','email','tel','url','search','number','password'].includes(el.type)&&!customHint?'text':'unsupported';
    const protectedField=protectedRE.test([label,el.id,el.getAttribute('name'),el.type].join(' '));
    const multiple=!!el.closest('.phoenix-select__multiValue,.ud__select__selector-multiple')||!!(phoenixSelect&&phoenixOwner.querySelector('.phoenix-select__multiValue'))||!!(udSelect&&el.closest('.ud__select').querySelector('.ud__select__selector-multiple'))||!!el.multiple || el.getAttribute('aria-multiselectable')==='true'||!!el.closest('.ant-select-multiple,.atsx-select-selection--multiple')||!!(atsxSelect&&el.querySelector('.atsx-select-selection--multiple'))||!!(mokaSelect&&el.closest('[class*="sd-Select-container"]').querySelector('[class*="sd-Input-tag-container"]'));
    const elementOwner=elementSelect?el.closest('.el-select'):null;
    const elementMenuOpen=!!(elementOwner&&(
      [...elementOwner.querySelectorAll('.el-select-dropdown')].some(visible)||
      (elementOwner.contains(document.activeElement)&&[...document.querySelectorAll('.el-select-dropdown')].some(visible))));
    const menuOpen=kind==='combobox'&&(el.getAttribute('aria-expanded')==='true'||elementMenuOpen||
      !!el.closest('.ant-select-open,.atsx-select-open'));
    const closedBlurredRoleCombo=role==='combobox'&&!elementSelect&&!menuOpen&&!el.contains(document.activeElement);
    const frameworkOpen=!!((antSelect&&[...document.querySelectorAll('.ant-select-dropdown')].some(visible))||(atsxSelect&&[...document.querySelectorAll('.atsx-select-dropdown')].some(visible))||(mokaSelect&&[...document.querySelectorAll('[class*="sd-Select-menu-"]')].some(visible))||(phoenixSelect&&[...document.querySelectorAll('.phoenix-selectList')].some(visible))||(udSelect&&[...document.querySelectorAll('.ud__select__dropdown')].some(visible)));
    const valueReadable=!frameworkOpen&&blockingDialogs.length===0&&(kind!=='combobox'||(!menuOpen&&(frameworkSelect||
      !!el.getAttribute('aria-valuetext')||nextSelect||(elementSelect&&(el.readOnly||document.activeElement!==el))||closedBlurredRoleCombo)));
    let value=null,uploadReady=null;
    // Protected values are never read into the snapshot.
    if(!protectedField){
      if(kind==='select')value=multiple?[...el.selectedOptions].map(o=>o.label):el.selectedOptions[0]?.label || '';
      else if(kind==='radio_group')value=el.matches('.phoenix-radio-group')?text(el.querySelector('.phoenix-radio--checked .phoenix-radio__radio-text')):text(el.querySelector('input[type="radio"]:checked')?.closest('label'));
      else if(kind==='checkbox'||kind==='radio')value=el.closest('.next-checkbox')&&['true','false'].includes(el.getAttribute('aria-checked'))?
        el.getAttribute('aria-checked')==='true':!!el.checked;
      else if(kind==='combobox') {
        const placeholder=el.tagName==='INPUT'?(el.getAttribute('placeholder')||''):'';
        const committedPlaceholder=(elementSelect&&!menuOpen&&!el.value&&placeholder&&
          !/^(请选择|请搜索|请输入|请填写)/.test(placeholder))?placeholder:'';
        value=el.getAttribute('aria-valuetext') || (el.tagName==='INPUT'?(el.value||committedPlaceholder):
          el.querySelector('input')?.value || text(el));
        const legacyAnt=el.closest('.ant-select');
        if(legacyAnt)value=text(legacyAnt.querySelector('.ant-select-selection-item,.ant-select-selection-selected-value'));
        if(mokaSelect)value=multiple?[...el.closest('[class*="sd-Select-container"]').querySelectorAll('[class*="sd-Tag-text"],[class*="sd-Tag-label"],[class*="sd-Tag-content"]')].map(text):text(el.closest('[class*="sd-Select-container"]').querySelector('[class*="sd-Input-display-value"]'));
        if(phoenixSelect)value=text(el.closest('.phoenix-select').querySelector('.phoenix-select__tipEle'));
        if(atsxSelect)value=multiple?[...el.closest('.atsx-select').querySelectorAll('.atsx-select-selection__choice__content')].map(e=>e.querySelectorAll('.select-item-label').length>1?null:text(e.querySelector('.select-item-label')||e)):text(el.closest('.atsx-select').querySelector('.atsx-select-selection-selected-value'));
        if(udSelect)value=text(el.closest('.ud__select').querySelector('.ud__select__selector__selection,.ud__select__selector__selectItem'));
        if(nextSelect){
          const owner=el.closest('.next-select');
          value=menuOpen?null:owner.classList.contains('next-select-tag')?
            [...owner.querySelectorAll('.next-select-values .next-tag-body')].map(text).join('\n'):
            text(owner.querySelector('.next-select-values > em'));
        }
      }
        else if(kind==='file'){
          const uploader=el.closest('.apply-form-uploader,.el-upload');
          const name=text(uploader?.querySelector('.apply-form-uploader__wrap-text'));
          const preview=uploader?.querySelector('.uploader-img img,.el-upload-list__item.is-success');
          value=name||(preview?'uploaded':'');
          const previewURL=preview?.getAttribute?.('src')||'';
          uploadReady=!!name||/^(?:https?:)?\/\//i.test(previewURL);
          const elementUpload=el.closest('.upload-demo');
          if(elementUpload&&elementUpload.querySelectorAll('input[type=file]').length===1){
            const items=[...elementUpload.querySelectorAll('.el-upload-list__item')].filter(visible);
            const item=items.length===1?items[0]:null;
            value=item?text(item.querySelector('.el-upload-list__item-name')):'';
            uploadReady=!!value&&item.classList.contains('is-success')&&
              !elementUpload.querySelector('.is-uploading,.is-fail,[aria-busy="true"],[role="progressbar"]');
          }
          const gradeProof=el.closest('.grade-prove__wrapper');
          const avatarUpload=el.closest('.avatar-uploader');
          if(avatarUpload&&avatarUpload.querySelectorAll('input[type=file]').length===1){
            const avatar=avatarUpload.querySelector('.avatar');
            const image=avatar?.style?.backgroundImage||'';
            const selected=el.files?.length===1?el.files[0]:null;
            value=selected&&/^url\(["']?(?:blob:|https?:)/.test(image)?selected.name:'';
            uploadReady=!!value&&!!el.closest('.el-form-item.is-success');
          }
          if(gradeProof){
            const items=[...gradeProof.querySelectorAll('.el-upload-list__item')].filter(visible);
            const item=items.length===1?items[0]:null;
            value=item?text(item.querySelector('.el-upload-list__item-name')):'';
            uploadReady=!!value&&item.classList.contains('is-success')&&
              !gradeProof.querySelector('.is-uploading,.is-fail,[aria-busy="true"],[role="progressbar"]');
          }
          const moka=el.closest('.file_upload');
          if(moka){
            const receipt=text(moka.querySelector('button.file_upload-btn'));
            const busy=!!moka.querySelector('[aria-busy="true"],[role="progressbar"],[class*="loading"],[class*="Loading"]');
            const filename=/\.[a-z0-9]{2,8}$/i.test(receipt)&&!/[\r\n]/.test(receipt);
            value=filename?receipt:'';
            uploadReady=filename&&!busy;
          }
          const mokaFiles=el.closest('[class*="sd-Upload-upload-wrap-"]');
          if(mokaFiles&&mokaFiles.querySelectorAll('input[type=file]').length===1){
            const names=[...mokaFiles.querySelectorAll('[class*="sd-Upload-file-name-"]')].filter(visible);
            const busy=!!mokaFiles.querySelector('[aria-busy="true"],[role="progressbar"],[class*="loading"],[class*="Loading"],[class*="sd-Upload-error"]');
            const name=names.length===1?text(names[0]):'';
            const item=names.length===1?names[0].closest('[class*="sd-Upload-list-"]'):null;
            value=name;
            uploadReady=!!name&&!busy&&!!item?.querySelector('[class*="sd-Upload-delete-icon-"]');
          }
          const atsx=el.closest('.uploadResume');
          if(atsx){
            const names=[...atsx.querySelectorAll('.uploadFile-loadedFilename')];
            const name=names.length===1?text(names[0]):'';
            const busy=!!atsx.querySelector('.atsx-upload-list-item-uploading,.atsx-upload-list-item-error,[role="progressbar"],[aria-busy="true"]');
            value=name;
            const matchingDone=[...atsx.querySelectorAll('.atsx-upload-list-item-done')].some(item=>
              text(item.querySelector('.atsx-upload-list-item-name-text'))===name);
            uploadReady=!!name&&!busy&&matchingDone;
          }
          const resume=el.closest('.resume-file');
          if(resume){
            const lists=[...resume.querySelectorAll('.file-list')].filter(visible);
            const names=lists.length===1?[...lists[0].querySelectorAll('.file__name')].filter(visible):[];
            const name=names.length===1?text(names[0]):'';
            const download=lists.length===1?lists[0].querySelector('a[download][href]'):null;
            const busy=[...resume.querySelectorAll('[aria-busy="true"],[role="progressbar"],.is-uploading,.is-fail')].some(visible);
            value=name;
            uploadReady=!!name&&!busy&&!!download&&/^https?:\/\//i.test(download.getAttribute('href'));
          }
      }
      else if(phoenixDate)value=text(phoenixOwner.querySelector('.phoenix-select__tipEle'));
      else if(kind==='text'||el.tagName==='INPUT'||el.tagName==='TEXTAREA')value=el.value;
      if(dateComponent==='moka-date'&&el.closest('label.day_info')&&/^\d{4}-\d{2}\s*\(\d{1,3}岁\)$/.test(value||''))value=value.slice(0,7);
    }
      const signature={tag:el.tagName,type:el.getAttribute('type') || '',role,label,name:el.getAttribute('name') || ''};
      const rangeDate=el.closest('.month-range-select');
      const dateParts=rangeDate?[...rangeDate.querySelectorAll('[class*="sd-Select-container"] input')]:[...(el.closest('[class*="apply-field-"]')?.querySelectorAll('input')||[])].filter(e=>['年','月'].includes(e.getAttribute('placeholder')));
      const datePartIndex=dateParts.length===4?dateParts.indexOf(el):-1;
      const singleDatePartIndex=dateParts.length===2&&(rangeDate||dateParts[0].getAttribute('placeholder')==='年'&&dateParts[1].getAttribute('placeholder')==='月')?dateParts.indexOf(el):-1;
      const elementPair=elementDate?[...(el.closest('.el-form-item')?.querySelectorAll('.el-date-editor input')||[])]:[];
      const elementPairIndex=elementPair.length===2?elementPair.indexOf(el):-1;
      const udPair=[...(el.closest('.throne-biz-date-range-picker-wrapper')?.querySelectorAll('input.ud__native-input')||[])];
      const udPairIndex=udPair.length===2?udPair.indexOf(el):-1;
    const educationRecord=el.closest(educationRowSelector);
    return {id:selector || 'unresolved:'+index,selector,label,kind,value,signature,protected:protectedField,
      ...(educationRecord&&educationCollections.includes(educationRecord.parentElement)?{record_container:documentPath(educationRecord)}:{}),
      search_evidence:kind==='combobox'?{
        editable:el.tagName==='INPUT'&&!el.readOnly&&!el.disabled,
        selection_structure:!(frameworkDate||elementDate||antDate||el.closest('.next-date-picker,.next-range-picker,.next-date-picker2,.next-range-picker2'))&&
          (elementSelect||nextSelect||role==='combobox'&&![ 'dialog','grid','tree'].includes(el.getAttribute('aria-haspopup'))),
        autocomplete:el.getAttribute('aria-autocomplete')||null
      }:undefined,
      value_readable:valueReadable,
        control_pattern:udPairIndex>=0?'ud_date_pair':datePartIndex>=0?'year_month_range_parts':elementPairIndex>=0?'element_date_pair':el.closest('.next-range-picker')?'next_range_date':el.closest('.ant-picker')&&el.closest('.ant-form-item')?.querySelectorAll('.ant-picker input').length===2?'ant_picker_pair':undefined,
        date_part:datePartIndex>=0?(datePartIndex%2===0?'year':'month'):singleDatePartIndex>=0?(singleDatePartIndex===0?'year':'month'):undefined,
        ...(udPairIndex>=0?{date_precision:'month'}:{}),
        range_endpoint:udPairIndex>=0?(udPairIndex===0?'start':'end'):datePartIndex>=0?(datePartIndex<2?'start':'end'):elementPairIndex>=0?(elementPairIndex===0?'start':'end'):el.closest('.next-range-picker')?
        ([...el.closest('.next-range-picker').querySelectorAll('.next-range-picker-trigger-input input')].indexOf(el)===0?'start':'end'):el.closest('.ant-picker')&&/^(开始|结束)时间$/.test(el.getAttribute('placeholder')||'')?(el.getAttribute('placeholder')==='开始时间'?'start':'end'):undefined,
      selection_mode:nextSelect&&el.closest('.next-select').classList.contains('next-select-tag')?'tag':undefined,
      control_status:kind==='text'&&!elementDate&&!antDate&&!frameworkDate&&!frameworkText&&!el.matches('.ant-input')?'unverified_text_candidate':'recognized',
      value_present:protectedField?!!el.value:['checkbox','radio'].includes(kind)?value===true:value!==null&&value!==''&&(!Array.isArray(value)||value.length>0),
      upload_ready:kind==='file'?uploadReady:undefined,
      component:el.matches('.phoenix-radio-group')?'phoenix-radio':hierarchy?'moka-hierarchy':udSelect?'ud-select':antSelect?'ant-select':mokaSelect?'moka-select':phoenixSelect?'phoenix-select':atsxSelect?'atsx-select':frameworkDate?dateComponent:frameworkText?'framework-text':kind==='combobox'&&el.tagName==='INPUT'&&!el.readOnly&&!elementSelect&&!nextSelect&&el.getAttribute('aria-controls')&&el.getAttribute('aria-autocomplete')==='list'?'aria-search-select':kind==='checkbox'&&el.closest('.next-checkbox')?'next-checkbox':elementSelect?'element-select':nextSelect?'next-select':elementDateNow?'element-date-now':elementDate?'element-date':antDate?'ant-date':kind==='file'?'native-file':el.matches('.ant-input')?'ant-text':null,
      readonly:!!el.readOnly,
      max_length:Number.isInteger(el.maxLength)&&el.maxLength>0?el.maxLength:null,
      date_precision:frameworkDate&&(el.getAttribute('placeholder')==='YYYY-MM'||dateComponent==='moka-date'&&el.closest('label.day_info')&&label==='出生日期 (年龄)')?'month':undefined,
      disabled:!!el.disabled || el.getAttribute('aria-disabled')==='true' || (!!el.readOnly&&kind!=='combobox'&&!antDate&&!elementDate&&!frameworkDate),
      required:(()=>{
        const explicit=!!el.required||el.getAttribute('aria-required')==='true';
        const item=el.closest('.el-form-item');
        const groupedChoice=['checkbox','radio'].includes(kind)&&
          (item?.querySelectorAll?.(`input[type="${kind}"]`)?.length||0)>1;
        return explicit||(!groupedChoice&&(!!item?.classList?.contains('is-required')||
          !!el.closest('[class*="apply-field-"]')?.querySelector(':scope > [class*="title-"] [class*="required-asterisk-"]')||
          !!el.closest('.ant-form-item')?.querySelector('.ant-form-item-required')||!!el.closest('.form-item')?.querySelector('.form-item__required')||!!el.closest('.ud-formily-item')?.querySelector('.ud-formily-item-label-asterisk')));
      })(),multiple,
      controls:el.getAttribute('aria-controls') || el.getAttribute('aria-owns') || '',
      expanded:kind==='combobox'?String(!!menuOpen):el.getAttribute('aria-expanded'),
      options:kind==='radio_group'&&el.matches('.phoenix-radio-group')?[...el.querySelectorAll('.phoenix-radio')].map(o=>({label:text(o.querySelector('.phoenix-radio__radio-text')),selector:selectorOf(o),disabled:o.classList.contains('phoenix-radio--disabled')})):kind==='radio_group'?[...el.querySelectorAll('input[type="radio"]')].map(o=>({label:text(o.closest('label')),value:o.value,selector:selectorOf(o),disabled:o.disabled})):
        kind==='select'?[...el.options].map(o=>({label:o.label,value:o.value,disabled:o.disabled})):[]};
  });
  const saveNodes=saveControl?[...root.querySelectorAll(saveControl)].filter(e=>!saveLabel||visible(e)&&text(e)===saveLabel):[];
  const save=saveNodes.length===1?{selector:saveLabel?selectorOf(saveNodes[0]):saveControl,label:text(saveNodes[0]),
    enabled:!saveNodes[0].disabled,signal:savedSignal}:null;
  return {url:location.href,fields,save,...(recordBoundary?{record_boundary:recordBoundary}:{})};
}

// DOM-only evidence. Never read application internals or execute page handlers.
export function readControlEvidence({moduleSelector,fieldSelector=null,semanticFields=[]}) {
  const visible=e=>!!(e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
  const path=e=>{if(!e)return null;const parts=[];while(e&&e!==document.documentElement){const peers=[...e.parentElement.children].filter(n=>n.tagName===e.tagName);parts.unshift(e.tagName.toLowerCase()+':nth-of-type('+(peers.indexOf(e)+1)+')');e=e.parentElement;}return 'html > '+parts.join(' > ');};
  const describe=e=>({selector:path(e),tag:e.tagName,type:e.getAttribute('type'),role:e.getAttribute('role'),class_name:String(e.className),placeholder:e.getAttribute('placeholder'),readonly:!!e.readOnly,disabled:!!e.disabled,
    label:e.getAttribute('aria-label')||[...(e.labels||[])].map(l=>l.textContent.trim()).join(' '),
    text:e.matches('button,[role="button"],option')?e.textContent.trim().slice(0,80):null,
    options:e.tagName==='SELECT'?[...e.options].map(o=>({label:o.label,value:o.value,selected:o.selected})):undefined});
  const roots=[...document.querySelectorAll(moduleSelector)];
  const semantic=e=>{
    if(roots.length!==1)return null;
    const matches=semanticFields.filter(f=>{
      if(!f.selector)return false;
      const nodes=[...roots[0].querySelectorAll(f.selector)];
      return nodes.length===1&&nodes[0]===e;
    });
    return matches.length===1?matches[0]:null;
  };
  const dialogs=[...document.querySelectorAll('[role="dialog"],.el-dialog,.el-message-box')]
    .filter(el=>visible(el)&&!(roots.length===1&&(el===roots[0]||el.contains(roots[0]))));
  const targets=roots.length===1&&fieldSelector?[...roots[0].querySelectorAll(fieldSelector)]:null;
  return {root_count:roots.length,target_count:targets?targets.length:null,menus:[...document.querySelectorAll('[role="listbox"],.el-select-dropdown,.el-picker-panel')].filter(e=>visible(e)&&!e.closest('.next-nav,nav,[role="navigation"]')).map(e=>({selector:path(e)})),dialogs:dialogs.map(d=>({selector:path(d),title:d.querySelector('.el-dialog__title,[role="heading"],h2,h3')?.textContent.trim()||'',
    controls:[...d.querySelectorAll('input,select,button,[role="combobox"]')].filter(visible).map(describe)})),
    fields:roots.length===1?[...roots[0].querySelectorAll('input,textarea,select')].filter(visible).map(e=>({...describe(e),matches_target:targets?.includes(e)||false,
      semantic_id:semantic(e)?.id,label:semantic(e)?.label||e.closest('.el-form-item')?.querySelector('label')?.textContent.trim()||'',parent_class:String(e.parentElement.className),container_class:String(e.closest('.el-form-item')?.className||'')})):[]};
}
