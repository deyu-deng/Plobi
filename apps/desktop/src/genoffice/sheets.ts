import { bootstrapOpenDeskOfficeSheetsEditor } from '@bitclub.ai/opendesk-office/sheets-renderer'

const filePath = new URLSearchParams(window.location.search).get('file')

void bootstrapOpenDeskOfficeSheetsEditor({ filePath })
