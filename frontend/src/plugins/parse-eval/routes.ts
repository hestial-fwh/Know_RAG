import './eval.css'
import ParseEvalHomeView from './ParseEvalHomeView.vue'
import ParseEvalDocumentView from './ParseEvalDocumentView.vue'
import ParseEvalRunView from './ParseEvalRunView.vue'

export default [
  { path: '/parse-eval', component: ParseEvalHomeView },
  { path: '/parse-eval/documents/:docId', component: ParseEvalDocumentView },
  { path: '/parse-eval/runs/:runId', component: ParseEvalRunView }
]
