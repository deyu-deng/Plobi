import { bootstrapOpenDeskOfficeWordEditor } from '@bitclub.ai/opendesk-office/word-renderer'

const filePath = new URLSearchParams(window.location.search).get('file')

void bootstrapOpenDeskOfficeWordEditor({ filePath })
