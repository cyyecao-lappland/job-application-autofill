// Explicit draft fallback; never interpret the selected option as a personal fact.
export const FAILURE_TEXT = '识别失败！需人工填写！';
export const FIRST_OPTION = '__FIRST_ENABLED_OPTION__';
export function firstEnabledOption(options) {
  return options.find(option => !option.disabled && String(option.label || '').trim() &&
    !/^(?:请选择|请选择.*|please select.*|select(?: an?| one)?(?: option)?|--+.*)$/i.test(String(option.label).trim()) &&
    option.value !== '');
}
