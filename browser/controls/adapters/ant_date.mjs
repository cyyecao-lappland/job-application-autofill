import {Deferred,milliseconds,fieldLocator} from '../runtime.mjs';

export const antDate = {
  declaration:{"name": "ant_date_input_v1", "adapterVersion": 1, "targetTypes": ["date"], "protocolVersion": 1},
  async applyField({tab,packet,field,op,end,markAction,readField}) {
    const target=fieldLocator(tab,packet,field);
    if(!/^\d{4}-\d{2}-\d{2}$/.test(op.value))throw new Deferred('date_requires_confirmed_day');
    const calendar=tab.playwright.locator('.ant-calendar-picker-container:visible input.ant-calendar-input');
    if(await calendar.count()!==0)throw new Deferred('preexisting_calendar');
    markAction();await target.click({timeoutMs:milliseconds(end)});
    if(await calendar.count()!==1)throw new Deferred('calendar_not_unique');
    await calendar.fill(op.value,{timeoutMs:milliseconds(end)});
    await calendar.press('Enter',{timeoutMs:milliseconds(end)});

  }
};
