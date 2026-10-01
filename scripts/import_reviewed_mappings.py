"""Import independently reviewed user corrections using real saved UI evidence.

This is an onboarding path for already-filled facts, not a browser writer or a
claim that all unrelated form fields have been filled. All rules activate through
the same post-save KnowledgeStore gate used by the application graph.
"""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge_form_graph.contracts import compile_plan, page_matches, redacted_profile
from edge_form_graph.knowledge import KnowledgeStore, saved_fields_equal


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def prepare(profile, rules, before, receipt, review):
    labels = [r['label'] for r in rules['rules']]
    if (review.get('approved') is not True or review.get('issues') or not review.get('reviewer')
            or set(review.get('snapshot_ids', [])) != {s['snapshot_id'] for s in before}
            or sorted(review.get('checked_rule_labels', [])) != sorted(labels)
            or review.get('reviewed_rules') != rules['rules']):
        raise ValueError('independent_rule_review_missing_or_stale')
    post_by_id = {s['module_id']: s for s in receipt['evidence'].get('saved_modules', [])}
    sections = {'个人基本信息': 'personalInfo', '实习经历': 'internship', '其它': 'other'}
    conversions = {'identity': 'identity', 'boolean_to_present_absent': 'present_absent', 'boolean_to_yes_no': 'yes_no'}
    modules = []
    for original in before:
        current = copy.deepcopy(post_by_id[original['module_id']])
        current['mapping_context'] = copy.deepcopy(original['mapping_context'])
        if not saved_fields_equal(current['fields'], original['fields']):
            raise ValueError('reviewed_page_changed')
        mappings = []
        learning_conditions = {}
        for rule in rules['rules']:
            if sections[rule['section']] != original['module_id']:
                continue
            fields = [f for f in current['fields'] if f['label'] == rule['label']]
            if len(fields) != 1:
                raise ValueError('rule_field_not_unique')
            field = fields[0]
            if field['kind'] == 'radio_group' and sorted(o['label'] for o in field['options']) != sorted(rule['option_labels']):
                raise ValueError('rule_options_changed')
            ref = rule['source']
            if 'pointer' in ref:
                pointer = ref['pointer']
            else:
                records = profile[ref['collection'].lstrip('/')]
                indices = [i for i, r in enumerate(records) if r.get('record_id') == ref['record_id']]
                if len(indices) != 1:
                    raise ValueError('source_record_not_unique')
                pointer = ref['collection'] + '/' + str(indices[0]) + '/' + ref['field']
            deps = []
            if rule['label'] == '若有，请写明职务':
                parent = [f for f in current['fields'] if f['label'] == '学生会经历']
                if len(parent) != 1 or parent[0]['value'] != '有' or profile['personal_answers']['has_student_union_experience'] is not True:
                    raise ValueError('student_role_dependency_not_met')
                deps = [parent[0]['id']]
                learning_conditions[field['id']] = [{'pointer': '/personal_answers/has_student_union_experience', 'equals': True}]
            mappings.append({'field_id': field['id'], 'source': pointer,
                             'transform': conversions[rule['conversion']], 'depends_on': deps})
        mapped = {m['field_id'] for m in mappings}
        proposal = {'mappings': mappings, 'deferred': [
            {'field_id': f['id'], 'reason': 'outside_reviewed_import'} for f in current['fields'] if f['id'] not in mapped]}
        operations, _ = compile_plan(profile, current, proposal)
        if not page_matches(operations, current):
            raise ValueError('profile_does_not_match_saved_answer')
        modules.append({'snapshot': original, 'current': current, 'proposal': proposal, 'operations': operations,
                        'revision': 0, 'reviewed_revision': 0, 'command': None, 'learning_conditions': learning_conditions,
                        'review': {'approved': True, 'issues': [], 'checked_field_ids': [f['id'] for f in current['fields']],
                                   'external_review': review, 'scope': 'reviewed_rules_and_unchanged_background'},
                        'results': {op['id']: {'status': 'already_matched'} for op in operations}})
    return modules


def main():
    parser = argparse.ArgumentParser()
    for name in ('profile', 'rules', 'before', 'receipt', 'review', 'knowledge', 'report'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    profile = redacted_profile(load(args.profile))
    rules, before, receipt, review = map(load, (args.rules, args.before, args.receipt, args.review))
    modules = prepare(profile, rules, before, receipt, review)
    store = KnowledgeStore(args.knowledge)
    counts = store.compile_saved(profile, modules, receipt)
    hits = []
    for module in modules:
        plan = store.known_plan(profile, module['current'])
        ops, _ = compile_plan(profile, module['current'], plan)
        imported = {op['id'] for op in module['operations']}
        matched = [op for op in ops if op['id'] in imported]
        if len(matched) != len(imported) or not page_matches(matched, module['current']):
            raise ValueError('activated_mapping_not_reusable')
        hits.extend({'module': module['current']['module_id'], 'label': op['field']['label'], 'transform': op['transform']} for op in matched)
    report = {'status': 'active_mapping_reuse_verified', **counts, 'mapped_fields': len(hits),
              'semantic_model_calls': 0, 'browser_writes': 0, 'reloaded': receipt['evidence'].get('reloaded', False),
              'knowledge_file': str(Path(args.knowledge).resolve()), 'rules': hits,
              'scope': 'Only the independently reviewed imported fields; not the entire application.'}
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
