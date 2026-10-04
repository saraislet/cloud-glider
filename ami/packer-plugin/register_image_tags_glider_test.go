// SPDX-License-Identifier: MPL-2.0
package ebssurrogate

import (
	"context"
	"errors"
	"io"
	"reflect"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/ec2"
	ec2types "github.com/aws/aws-sdk-go-v2/service/ec2/types"
	"github.com/hashicorp/packer-plugin-amazon/common/clients"
	"github.com/hashicorp/packer-plugin-sdk/multistep"
	packersdk "github.com/hashicorp/packer-plugin-sdk/packer"
)

type captureRegistration struct {
	clients.Ec2Client
	input *ec2.RegisterImageInput
}

func (c *captureRegistration) RegisterImage(ctx context.Context, input *ec2.RegisterImageInput, opts ...func(*ec2.Options)) (*ec2.RegisterImageOutput, error) {
	c.input = input
	return nil, errors.New("stop after capturing request; no AWS calls")
}
func TestGliderRegisterImageTags(t *testing.T) {
	for _, invalid := range []bool{false, true} {
		t.Run(map[bool]string{false: "required_tags_at_creation", true: "invalid_tag_prevents_registration"}[invalid], func(t *testing.T) {
			config := &Config{}
			config.AMIName = "cloud-glider-test"
			config.AMITags = map[string]string{"project": "cloud-glider", "purpose": "daemon-image", "approval": "candidate", "region": "{{ .BuildRegion }}"}
			if invalid {
				config.AMITags["project"] = "{{"
			}
			client := &captureRegistration{}
			state := new(multistep.BasicStateBag)
			state.Put("config", config)
			state.Put("ec2v2", client)
			state.Put("aws_config", &aws.Config{Region: "us-west-2"})
			state.Put("snapshot_ids", map[string]string{})
			state.Put("ui", &packersdk.BasicUi{Writer: io.Discard, ErrorWriter: io.Discard})
			step := newStepRegisterAMI(nil, nil)
			if step.Run(context.Background(), state) != multistep.ActionHalt {
				t.Fatal("expected halt")
			}
			if invalid {
				if client.input != nil {
					t.Fatal("registration called despite invalid tag")
				}
				return
			}
			if client.input == nil {
				t.Fatal("registration not called")
			}
			specs := client.input.TagSpecifications
			if len(specs) != 1 || specs[0].ResourceType != ec2types.ResourceTypeImage {
				t.Fatalf("wrong specifications: %#v", specs)
			}
			got := map[string]string{}
			for _, tag := range specs[0].Tags {
				got[aws.ToString(tag.Key)] = aws.ToString(tag.Value)
			}
			want := map[string]string{"project": "cloud-glider", "purpose": "daemon-image", "approval": "candidate", "region": "us-west-2"}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("tags: %#v", got)
			}
		})
	}
}
