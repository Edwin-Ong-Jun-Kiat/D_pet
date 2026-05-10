#include <Adafruit_PWMServoDriver.h>
#include <Wire.h>

#define servoPin 0
#define SERVOMIN 150
#define SERVOMAX 600

Adafruit_PWMServoDriver myServo1 = Adafruit_PWMServoDriver(0x40);

// Define sweep parameters
int minAngle = 0;    // Minimum servo angle
int maxAngle = 180;  // Maximum servo angle
int stepDelay = 15;  // Delay in ms between steps

uint8_t servonum = 0;
uint8_t numofServos = 1;

void setup() {
  myServo1.begin();
  myServo1.setPWMFreq(60);
  delay(10);

  Serial.begin(9600);
  Serial.println("TowerPro SG92R Servo Test Started");
}

void loop() {
  uint16_t pulselen;

  // Forward sweep: servos 0 through 8
  for (servonum = 0; servonum <= 8; servonum++) {
    for (pulselen = SERVOMIN; pulselen < SERVOMAX; pulselen++) {
      myServo1.setPWM(servonum, 0, pulselen);
    }
    delay(500);
  }

  // Reverse sweep: servos 8 through 0
  for (servonum = 8; servonum <= 8; servonum--) {
    for (pulselen = SERVOMAX; pulselen > SERVOMIN; pulselen--) {
      myServo1.setPWM(servonum, 0, pulselen);
    }
    delay(500);
    if (servonum == 0) break;
  }
}
