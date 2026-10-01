import test from 'node:test';
import assert from 'node:assert/strict';
import {identityField,readIdentityMatch} from '../browser/confidential_identity.mjs';

test('confidential allowlist excludes password OTP and ambiguous labels',()=>{
  for(const label of ['身份证号码','*证件号码：'])assert.equal(identityField({label,kind:'text',protected:true}),true);
  for(const label of ['验证码','密码','身份证号码及密码','证件类型'])assert.equal(identityField({label,kind:'text',protected:true}),false);
});
test('identity reader returns booleans only, including mismatch',()=>{
  const field={value:'synthetic-only'};
  globalThis.document={querySelectorAll:()=>[{querySelectorAll:()=>[field]}]};
  assert.deepEqual(readIdentityMatch({moduleSelector:'#module',selector:'#field',expected:'synthetic-only'}),{unique:true,present:true,match:true});
  assert.deepEqual(readIdentityMatch({moduleSelector:'#module',selector:'#field',expected:'different'}),{unique:true,present:true,match:false});
});
