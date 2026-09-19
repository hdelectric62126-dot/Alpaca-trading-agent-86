import unittest
from unittest.mock import patch
import pandas as pd
from after_hours_agent import AfterHoursLearningAgent, ResearchCandidate
from research_validation_agent import ResearchValidationAgent


def result(ret, trades=10):
    return dict(return_pct=ret, trades=trades, win_rate_pct=50)


class ResearchUpgradeTests(unittest.TestCase):
    def test_only_selected_candidate_and_baseline_reach_holdout(self):
        current = ResearchCandidate(.0035, 60, .0045, .005)
        other = ResearchCandidate(.005, 80, .0045, .005)
        third = ResearchCandidate(.006, 80, .0045, .005)
        agent = AfterHoursLearningAgent()
        calls = []
        def simulate(bars, **parameters):
            calls.append((len(bars), parameters))
            return result({.0035: 1, .005: 10, .006: -1}[parameters['dip_threshold']])
        with patch.object(agent, 'candidates', return_value=[current, other, third]), patch('after_hours_agent.walk_forward_backtest', side_effect=simulate):
            study = agent.study_symbol('AMD', pd.DataFrame({'close': [100] * 200}), current)
        self.assertEqual([size for size, _ in calls], [140, 140, 140, 60, 60])
        self.assertEqual(study['best_candidate']['parameters']['minimum_score'], 80)
        self.assertTrue(study['validation_qualified'])
        self.assertFalse(study['evidence_review']['live_strategy_validated'])

    def test_reject_positive_candidate_worse_than_baseline(self):
        review = ResearchValidationAgent().assess({'train': result(5), 'test': result(2)}, result(3))
        self.assertFalse(review['qualified_for_paper_review'])

    def test_require_samples_in_train_holdout_and_baseline(self):
        for train, test, baseline in ((result(10, 1), result(4), result(1)),
                                      (result(10), result(4, 1), result(1)),
                                      (result(10), result(4), result(1, 1))):
            self.assertFalse(ResearchValidationAgent().assess({'train': train, 'test': test}, baseline)['qualified_for_paper_review'])

    def test_ties_independent_of_candidate_order(self):
        current = ResearchCandidate(.0035, 60, .0045, .005)
        other = ResearchCandidate(.005, 80, .0045, .005)
        selected = []
        for candidates in ([current, other], [other, current]):
            agent = AfterHoursLearningAgent()
            with patch.object(agent, 'candidates', return_value=candidates), patch('after_hours_agent.walk_forward_backtest', return_value=result(2)):
                selected.append(agent.study_symbol('AMD', pd.DataFrame({'close': [100]*200}), current)['best_candidate']['parameters'])
        self.assertEqual(selected[0], selected[1])

    def test_nonfinite_result_rejected(self):
        review = ResearchValidationAgent().assess({'train': result(float('nan')), 'test': result(2)}, result(1))
        self.assertFalse(review['qualified_for_paper_review'])
    def test_cross_symbol_disagreement_cannot_recommend_parameters(self):
        current = ResearchCandidate(.0035, 60, .0045, .005)
        studies = [dict(validation_qualified=True, best_candidate=dict(parameters={'dip_threshold': dip}, test=result(5))) for dip in (.001, .002, .003, .004)]
        self.assertIn('disagree', AfterHoursLearningAgent._recommend(studies, current))
    def test_bad_symbol_does_not_abort_other_studies(self):
        import tempfile
        import json
        from pathlib import Path
        current = ResearchCandidate(.0035, 60, .0045, .005)
        with tempfile.TemporaryDirectory() as directory:
            agent = AfterHoursLearningAgent(Path(directory) / 'report.json')
            with patch.object(agent, 'candidates', return_value=[current]), patch('after_hours_agent.walk_forward_backtest', return_value=result(2)):
                report = agent.run({'BAD': None, 'GOOD': pd.DataFrame({'close': [100]*200})}, current)
            self.assertEqual([study['status'] for study in report['studies']], ['invalid_data', 'studied'])
            json.dumps(report, allow_nan=False)
            self.assertTrue(agent.output_path.exists())

    def test_nonfinite_training_candidate_discarded_before_selection(self):
        current = ResearchCandidate(.0035, 60, .0045, .005)
        other = ResearchCandidate(.005, 80, .0045, .005)
        agent = AfterHoursLearningAgent()
        with patch.object(agent, 'candidates', return_value=[other, current]), patch('after_hours_agent.walk_forward_backtest', side_effect=[result(float('nan')), result(2), result(1)]):
            study = agent.study_symbol('AMD', pd.DataFrame({'close': [100]*200}), current)
        self.assertEqual(study['best_candidate']['parameters']['minimum_score'], 60)
        self.assertEqual(study['discarded_candidates'], 1)

    def test_all_invalid_training_skips_holdout(self):
        current = ResearchCandidate(.0035, 60, .0045, .005)
        agent = AfterHoursLearningAgent()
        with patch.object(agent, 'candidates', return_value=[current]), patch('after_hours_agent.walk_forward_backtest', return_value=result(float('inf'))) as simulate:
            study = agent.study_symbol('AMD', pd.DataFrame({'close': [100]*200}), current)
        self.assertEqual(study['status'], 'invalid_data')
        self.assertEqual(simulate.call_count, 1)

    def test_nonfinite_holdout_is_not_serialized(self):
        import json
        current = ResearchCandidate(.0035, 60, .0045, .005)
        agent = AfterHoursLearningAgent()
        with patch.object(agent, 'candidates', return_value=[current]), patch('after_hours_agent.walk_forward_backtest', side_effect=[result(2), result(float('nan'))]):
            study = agent.study_symbol('AMD', pd.DataFrame({'close': [100]*200}), current)
        self.assertEqual(study['status'], 'invalid_data')
        json.dumps(study, allow_nan=False)
