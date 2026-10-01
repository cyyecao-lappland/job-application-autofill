"""Business-field lookup only: (canonical profile module, label) -> source.

No DOM, control kinds, browser handlers, or personal values are used here.
The existing saved mapping file remains authoritative. Its portable entries are
projected into this dict at load time; site-specific rules stay in the old index.
"""
import copy
import json
import re
import unicodedata
from urllib.parse import urlsplit


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value)).casefold().split()).strip(' *:：')


MODULE_ALIASES = {
    'personal': ('personal', 'personalInfo', 'personal_info', 'basic_info', 'identity', 'contact',
                 '个人信息', '基本信息', '基础信息', '联系方式'),
    'education': ('education', 'educations', '教育', '教育情况', '教育经历', '教育背景'),
    'employment': ('employment', 'work', 'work_experience', 'internship', 'internships',
                   '工作经历', '实习经历', '实习'),
    'projects': ('projects', 'project', '项目', '项目经历'),
    'research': ('research', 'papers', 'publications', '论文', '论文发表', '研究经历'),
    'awards': ('awards', 'honors', '奖项', '获奖', '获奖情况'),
    'competitions': ('competitions', '竞赛', '竞赛经历'),
    'campus': ('campus', '校园', '校园经历'),
    'family': ('family', '家庭', '家庭成员'),
    'languages': ('language', 'languages', '语言', '语言能力'),
    'certifications': ('certifications', 'certificates', '证书', '资格证书'),
}
MODULE_NAMES = {normalize(alias): module for module, aliases in MODULE_ALIASES.items() for alias in aliases}


def module_name(value):
    label = normalize(value)
    return MODULE_NAMES.get(label) or MODULE_NAMES.get(re.sub(r'[-_]\d+$', '', label))


def profile_module(snapshot):
    context = snapshot.get('mapping_context') or {}
    # An explicitly bound project presented as an internship is still a project.
    collection = context.get('record_collection')
    if collection:
        return module_name(collection.lstrip('/'))
    for value in (context.get('module_type'), snapshot.get('module_label'), snapshot.get('module_id')):
        matched = module_name(value or '')
        if matched:
            return matched
    return None


LABEL_GROUPS = {
    'personal': (
        ('姓名', '您的姓名', '中文姓名', 'name', 'full name'),
        ('邮箱', '电子邮箱', '电子邮件', 'email', 'e-mail'),
        ('手机号码', '手机号', '手机', '联系电话', 'mobile', 'mobile phone'),
        ('出生日期', '出生年月', '出生日期 (年龄)', 'birth date', 'date of birth'),
        ('性别', 'gender'), ('民族', 'ethnicity'),
    ),
    'education': (
        ('学校', '学校名称', '学校全称', '毕业院校', 'school', 'university'),
        ('专业', '专业名称', 'major'), ('学历', '学历层次', 'education level'),
        ('入学时间', '入学日期', '开始时间', '起始日期', 'start date'),
        ('毕业时间', '毕业日期', '结束时间', '结束日期', 'end date'),
    ),
    'employment': (
        ('公司名称', '公司或组织名称', '公司/组织名称', '实习单位', '单位名称', 'company'),
        ('职位', '职位名称', '岗位', '岗位名称', 'position'),
        ('工作内容', '工作描述', '工作职责', '实习内容', 'responsibilities'),
    ),
}
LABEL_NAMES = {module: {normalize(alias): normalize(group[0]) for group in groups for alias in group}
               for module, groups in LABEL_GROUPS.items()}


def field_key(module, label):
    label = normalize(label)
    return module, LABEL_NAMES.get(module, {}).get(label, label)


# Schema mappings, not candidate answers. Missing paths remain unresolved.
DEFAULT_SOURCES = {
    ('personal', '姓名'): '/identity/name',
    ('personal', '邮箱'): '/contact/email',
    ('personal', '手机号码'): '/contact/mobile',
    ('personal', '出生日期'): '/identity/birth_date',
    ('personal', '性别'): '/identity/gender',
    ('personal', '民族'): '/identity/ethnicity',
    ('personal', '手机 国家/地区代码'): '/contact/mobile_country_code',
}


def portable_entry(scope, entry):
    """Only plain, same-module factual relations can lose their website scope.

    Conditions and control dependencies retain their original scope. Company
    answers, preferences, consent, unknown schemas and fixed records are never
    promoted by merely deleting their hostname or control kind.
    """
    module = module_name(scope[2])
    if not module or entry.get('status') != 'active' or entry.get('conditions') or entry.get('depends_on'):
        return False
    source = entry.get('source', {})
    if set(source) == {'pointer'}:
        root = source['pointer'].split('/')[1]
        return root in {'identity', 'contact'} and module == 'personal'
    return (set(source) == {'collection', 'relative'} and
            module_name(source['collection'].lstrip('/')) == module)


class FieldMappingIndex:
    def __init__(self, saved_entries):
        self.entries = {}
        self.scoped_entries = {}
        for encoded, entry in saved_entries.items():
            scope = json.loads(encoded)
            module = module_name(scope[2])
            key = field_key(module, scope[3])
            if entry.get('status') == 'conflicted' and module:
                self.entries[key] = {'status': 'conflicted'}
            if not portable_entry(scope, entry):
                scoped_key = (*scope[:2], *field_key(module or scope[2], scope[3]))
                prior = self.scoped_entries.get(scoped_key)
                if prior is None:
                    self.scoped_entries[scoped_key] = copy.deepcopy(entry)
                elif prior != entry:
                    self.scoped_entries[scoped_key] = {'status': 'conflicted'}
                continue
            candidate = {k: copy.deepcopy(entry[k]) for k in ('source', 'transform')}
            prior = self.entries.get(key)
            if prior is None:
                self.entries[key] = {'status': 'active', **candidate}
            elif prior != {'status': 'active', **candidate}:
                self.entries[key] = {'status': 'conflicted'}

    def scoped_lookup(self, scope, label):
        key = (*scope[:2], *field_key(module_name(scope[2]) or scope[2], label))
        return copy.deepcopy(self.scoped_entries.get(key))

    def lookup(self, snapshot, label):
        key = field_key(profile_module(snapshot), label)
        if key in self.entries:
            return copy.deepcopy(self.entries[key])
        education_boolean = {'是否专升本': 'is_top_up', '是否联合办学': 'is_joint_program',
                             '该学历是否为联合办学': 'is_joint_program'}.get(normalize(label))
        if key[0] == 'education' and education_boolean:
            return {'status': 'active', 'source': {'collection': '/education',
                    'relative': '/' + education_boolean}, 'transform': 'yes_no'}
        url = urlsplit(snapshot.get('target', {}).get('url', ''))
        tenant = url.path.strip('/').split('/')
        if (key == ('personal', '是否校园大使推荐') and url.hostname == 'app.mokahr.com'
                and len(tenant) >= 2 and tenant[0] in {'campus_apply', 'campus-recruitment'}
                and tenant[1] == 'shopee'):
            return {'status': 'active', 'source': {'collection': '/company_answers',
                    'record_id': 'company-shopee', 'relative': '/campus_ambassador'}, 'transform': 'yes_no'}
        if (url.hostname == 'poizon.jobs.feishu.cn'
                and normalize(snapshot.get('module_label', '')) == normalize('AI技能运用：')):
            ai_labels = {
                normalize('请列出你常用的 AI 工具 & 模型（编码工具：Cursor、GitHub Copilot...... 大模型：GPT‑4o、Claude 3.7 Sonnet、DeepSeek‑V3...... 应用平台：Coze 扣子......）'):
                    '/personal_answers/ai_tools_and_models_text',
                normalize('请描述一个与 AI 协作完成的项目或任务（请说明项目背景、遇到的问题、解决方案以及最终的结果）'):
                    '/personal_answers/ai_collaboration_project_answer',
            }
            pointer = ai_labels.get(normalize(label))
            if pointer:
                return {'status': 'active', 'source': {'pointer': pointer}, 'transform': 'identity'}
        if (key == ('education','学历') and urlsplit(snapshot.get('target',{}).get('url','')).hostname
                == 'campus-talent.alibaba.com'):
            return {'status':'active','source':{'collection':'/education','relative':'/education_level'},'transform':'education_category'}
        city_transform = ('china_city_path' if urlsplit(snapshot.get('target',{}).get('url','')).hostname
                          == 'campus-talent.alibaba.com' else 'address_city')
        if key == ('personal', '家庭所在城市'):
            return {'status':'active','source':{'pointer':'/contact/home_address'},'transform':city_transform}
        if key == ('personal', '学校所在城市'):
            return {'status':'active','source':{'collection':'/education','current':True,'relative':'/campus_location'},'transform':city_transform}
        if key == ('personal', '年龄'):
            return {'status':'active','source':{'pointer':'/identity/birth_date'},'transform':'age_from_birth_date'}
        pointer = DEFAULT_SOURCES.get(key)
        if pointer:
            return {'status': 'active', 'source': {'pointer': pointer}, 'transform': 'identity'}
        return None
