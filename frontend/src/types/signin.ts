export interface SignInResult {
  success: boolean
  status: 'unsigned' | 'signed' | 'already_signed' | 'login_expired' | 'invalid_role'
    | 'network_error' | 'captcha_required' | 'error'
  msg: string
  signed_today: boolean | null
  signed_days: number | null
  checked_date: string
  rewards: { name: string; count: number | null }[]
}
