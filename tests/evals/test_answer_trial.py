import unittest
import json
import tempfile
from pathlib import Path

from evals.answer_rubric import load_rubric
from evals.answer_trial import review_template, summarize_review, run_configuration


class AnswerTrialTest(unittest.TestCase):
    def test_run_resumes_completed_answers_and_reserves_budget_before_call(self):
        rubric = load_rubric(Path('evals/answer_questions.yaml'))
        observed = {source.document_id: source.source_sha256 for source in rubric.source_revisions.values()}
        with tempfile.TemporaryDirectory(dir='.local') as folder:
            root = Path(folder)
            ledger = root / 'budget.json'
            ledger.write_text(json.dumps({'stage5_ceiling_usd':8,'cumulative_ceiling_usd':10,
                'prior_stage4_usd':1.434878,'stage5_estimated_usd_upper_bound':0.000002128}))
            calls = []
            def invoke(args):
                calls.append(args)
                path = Path(args[args.index('--report')+1])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'status':'completed','estimated_usd':'0.01',
                    'answer':'A Unicode example: \u201chello\u201d',
                    'usage_complete':True,'configuration':{'source_revisions':observed}}, ensure_ascii=False), encoding='utf-8')
                return 0
            config = {'question_ids':['Q4'],'model':'openai/gpt-5-mini','reasoning_effort':'minimal',
                'generation_provider_tag':'azure','max_generation_usd_per_million':'2','max_usd':'0.25',
                'input_byte_budget':400000}
            run_configuration(rubric, config, root/'pilot', ledger, observed, invoke=invoke)
            run_configuration(rubric, config, root/'pilot', ledger, observed, invoke=invoke)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][calls[0].index('--input-byte-budget') + 1], '400000')
            state = json.loads(ledger.read_text())
            self.assertAlmostEqual(state['stage5_estimated_usd_upper_bound'], 0.010002128)
            manifest_path = root/'pilot'/'pilot.json'
            interrupted = json.loads(manifest_path.read_text(encoding='utf-8'))
            interrupted['questions']['Q4'][-1]['status'] = 'reserved'
            manifest_path.write_text(json.dumps(interrupted), encoding='utf-8')
            state['attempts'][-1].update(status='reserved', cost_upper_bound='0.25')
            ledger.write_text(json.dumps(state), encoding='utf-8')
            run_configuration(rubric, config, root/'pilot', ledger, observed, invoke=invoke)
            self.assertEqual(len(calls), 1)
            self.assertAlmostEqual(json.loads(ledger.read_text())['stage5_estimated_usd_upper_bound'], 0.010002128)
            config['max_usd'] = '9'
            with self.assertRaises(ValueError):
                run_configuration(rubric, config, root/'different', ledger, observed, invoke=invoke)
            self.assertEqual(len(calls), 1)

    def test_unknown_cost_failure_keeps_reservation_and_requires_explicit_retry(self):
        rubric = load_rubric(Path('evals/answer_questions.yaml'))
        observed = {source.document_id: source.source_sha256 for source in rubric.source_revisions.values()}
        with tempfile.TemporaryDirectory(dir='.local') as folder:
            root = Path(folder)
            ledger = root/'budget.json'
            ledger.write_text(json.dumps({'stage5_ceiling_usd':8,'cumulative_ceiling_usd':10,
                'prior_stage4_usd':1.434878,'stage5_estimated_usd_upper_bound':0}))
            def failed(args):
                path = Path(args[args.index('--report')+1])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'status':'failed','usage_complete':False,'estimated_usd':None}))
                return 1
            config = {'question_ids':['Q4'],'model':'openai/gpt-5-mini','reasoning_effort':'minimal',
                'generation_provider_tag':'azure','max_generation_usd_per_million':'2','max_usd':'0.20'}
            result = run_configuration(rubric, config, root/'pilot', ledger, observed, invoke=failed)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(json.loads(ledger.read_text())['stage5_estimated_usd_upper_bound'], 0.20)
            with self.assertRaises(ValueError):
                run_configuration(rubric, config, root/'pilot', ledger, observed, invoke=failed)

    def test_unreviewed_items_are_not_counted_as_failures_or_passes(self):
        rubric = load_rubric(Path('evals/answer_questions.yaml'))
        review = review_template(rubric, 'Q5')
        result = summarize_review(rubric, review)
        self.assertEqual(result['facet_coverage'], {'covered': 0, 'partial': 0, 'missing': 0, 'unreviewed': 2})
        self.assertEqual(result['review_status'], 'pending')

    def test_examples_required_without_a_prescribed_example(self):
        rubric = load_rubric(Path('evals/answer_questions.yaml'))
        review = review_template(rubric, 'Q5')
        for facet in review['facets']:
            facet.update(rating='covered', answer_excerpt='Relevant explanation')
        review.update(examples={'rating': 'pass', 'reason': 'A different relevant example, labeled as an adaptation'},
                      qualifications={'rating': 'pass', 'reason': 'Limits preserved'},
                      claims=[{'claim': 'Set a deadline', 'support': 'supported', 'citation_ids': ['E1.0'], 'reason': 'Exact supporting note'}],
                      inference_failures=[], attribution_failures=[], usefulness='useful')
        result = summarize_review(rubric, review)
        self.assertEqual(result['review_status'], 'reviewed')
        self.assertEqual(result['facet_coverage']['covered'], 2)
        self.assertEqual(result['examples'], 'pass')

    def test_foreign_rubric_version_or_missing_facet_blocks_scoring(self):
        rubric = load_rubric(Path('evals/answer_questions.yaml'))
        review = review_template(rubric, 'Q1')
        review['rubric_version'] += 1
        with self.assertRaises(ValueError):
            summarize_review(rubric, review)
        review['rubric_version'] = rubric.version
        review['facets'].pop()
        with self.assertRaises(ValueError):
            summarize_review(rubric, review)
