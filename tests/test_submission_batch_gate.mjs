import test from 'node:test';
import assert from 'node:assert/strict';
import {settledForSubmission} from '../scripts/run_reviewed_submission.mjs';

test('batch submission requires settled complete scope coverage',()=>{
 const ready={status:'incomplete_coverage',pending_kind:null,manual_review_count:0,
  coverage_gaps:[{reason:'scope_draft'}]};
 assert.equal(settledForSubmission(ready),true);
 for(const change of [{pending_kind:'fill'},{status:'module_blocked'},{manual_review_count:1},
  {coverage_gaps:[{reason:'module_not_processed'}]},
  {coverage_gaps:[{reason:'scope_review_blocked'}]}]){
  assert.equal(settledForSubmission({...ready,...change}),false);
 }
});
