// Compatibility exports; implementations live in the control library.
export {classifyControl,classifyDialog,readControlEvidence} from './control_detection.mjs';
export {scopedControlTarget} from './controls/target.mjs';
export {parseISODate} from './controls/date_value.mjs';
export {runTextAdapter} from './controls/adapters/text.mjs';
export {locationKey,exactLocationOption,runLocationAdapter} from './controls/adapters/region.mjs';
export {exactAdministrativeOption,administrativeSearchQuery,runAdministrativeRegionAdapter} from './controls/adapters/administrative_region.mjs';
export {runElementDateAdapter} from './controls/adapters/element_date.mjs';
export {runElementDateNowAdapter} from './controls/adapters/element_date_now.mjs';
export {runNextRangeDateAdapter} from './controls/adapters/next_range_date.mjs';
