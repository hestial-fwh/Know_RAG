import { api } from './client'

export interface User {
  id: string
  username: string
  role: string
}

export interface CaptchaData {
  captcha_id: string
  image: string
}

export async function login(username: string, password: string, captchaId: string, captchaCode: string) {
  return api.post<{ access_token: string; token_type: string; user: User }>('/api/v1/auth/login', {
    username,
    password,
    captcha_id: captchaId,
    captcha_code: captchaCode
  })
}

export async function getCaptcha() {
  return api.get<CaptchaData>('/api/v1/auth/captcha')
}

export async function me() {
  return api.get<User>('/api/v1/auth/me')
}
