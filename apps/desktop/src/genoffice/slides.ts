import { bootstrapOpenDeskOfficeSlidesEditor } from '@bitclub.ai/opendesk-office/renderer'

const filePath = new URLSearchParams(window.location.search).get('file')

void bootstrapOpenDeskOfficeSlidesEditor({ filePath })
