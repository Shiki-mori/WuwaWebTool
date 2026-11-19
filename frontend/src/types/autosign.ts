export interface AutoSignLastRun {
  time: string
  date: string
  uid: string
  attempts: number
  success: boolean
  status: string
  msg: string
  signed_days: number | null
  rewards: { name: string; count: number | null }[]
}

export interface AutoSignStatus {
  supported: boolean
  enabled: boolean
  uid: string | null
  time: string | null
  task_installed: boolean
  task_time: string | null
  last_run: AutoSignLastRun | null
  msg: string
}

export interface AutoSignResponse {
  success: boolean
  msg: string
  status: AutoSignStatus
}
