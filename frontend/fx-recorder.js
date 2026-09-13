import fs from 'fs'

export function fxRecorder(logPath) {
  return {
    name: 'fx-recorder',
    configureServer(server) {
      server.middlewares.use('/__fx', (req, res) => {
        let body = ''
        req.on('data', (chunk) => { body += chunk })
        req.on('end', () => {
          fs.appendFileSync(logPath, body + '\n')
          res.statusCode = 204
          res.end()
        })
      })
    }
  }
}
